import json
import re
import unicodedata  #REMOVE ACENTO
from dataclasses import dataclass
from typing import Optional, Dict, List


@dataclass
class ParseResult:
    is_command: bool
    reason: str
    acao: Optional[str] = None
    objeto: Optional[str] = None
    local: Optional[str] = None
    comando_para_cliente: Optional[str] = None


def normalizar(texto: str) -> str:
    texto = texto.lower().strip() 
    texto = unicodedata.normalize("NFD", texto) 
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn") 
    texto = " ".join(texto.split()) 
    return texto


def _regex_frase(frase: str) -> re.Pattern:
    """
    Cria um regex que encontra a frase como "termo inteiro".
    Ex.: 'desligar' não deixa casar 'ligar' dentro.
    Funciona tanto para 1 palavra quanto para frases (ex: 'ar condicionado').
    """
    frase = re.escape(frase)
    # Bordas: antes não pode ser letra/número/_ e depois também não
    return re.compile(rf"(?<![\w]){frase}(?![\w])") #“lookbehind”: garante que antes não exista letra/número/_


class CommandParser:
    def __init__(self, json_path: str, wake_word: str = ""):
        with open(json_path, "r", encoding="utf-8") as f:
            self.cfg = json.load(f)

        self.keyword_command = normalizar(self.cfg.get("keyword_command", "comando"))
        self.wake_word = normalizar(wake_word or self.cfg.get("wake_word_default", ""))

        # Ações: normaliza e cria regex para cada sinônimo
        self.acoes: Dict[str, List[str]] = {
            k: [normalizar(x) for x in v] for k, v in self.cfg["acoes"].items()
        }

        # Objetos
        self.objetos = {}
        for obj_key, obj_data in self.cfg["objetos"].items():
            sinonimos = [normalizar(x) for x in obj_data["sinonimos"]]
            self.objetos[obj_key] = {
                "sinonimos": sinonimos,
                "requer_local": bool(obj_data.get("requer_local", False)),
                "permitir_geral": bool(obj_data.get("permitir_geral", True)),
            }

        # Locais
        self.locais: Dict[str, List[str]] = {
            k: [normalizar(x) for x in v] for k, v in self.cfg["locais"].items()
        }

        # Pré-compila regex e ordena sinônimos do maior pro menor (evita match parcial)
        self._acoes_regex = self._build_regex_table(self.acoes)
        self._locais_regex = self._build_regex_table(self.locais)
        self._objetos_regex = self._build_regex_objetos(self.objetos)

    def _build_regex_table(self, table: Dict[str, List[str]]):
        compiled = {}
        for key, synonyms in table.items():
            # Maior primeiro (ex.: "desligar" antes de "ligar")
            synonyms_sorted = sorted(synonyms, key=len, reverse=True)
            compiled[key] = [_regex_frase(s) for s in synonyms_sorted]
        return compiled

    def _build_regex_objetos(self, objetos):
        compiled = {}
        for obj_key, obj_data in objetos.items():
            synonyms_sorted = sorted(obj_data["sinonimos"], key=len, reverse=True)
            compiled[obj_key] = [_regex_frase(s) for s in synonyms_sorted]
        return compiled

    def set_wake_word(self, new_name: str) -> None:
        self.wake_word = normalizar(new_name)

    def _find_first_match_key(self, text: str, regex_table: Dict[str, List[re.Pattern]]) -> Optional[str]:
        """
        Retorna a chave canônica do PRIMEIRO match encontrado,
        mas varre priorizando o match mais forte:
        - sinônimos longos primeiro (já ordenados)
        - e escolha por "melhor posição" no texto
        """
        best_key = None
        best_pos = None

        for key, patterns in regex_table.items():
            for pat in patterns:
                m = pat.search(text)
                if m:
                    pos = m.start()
                    if best_pos is None or pos < best_pos:
                        best_pos = pos
                        best_key = key
                    break  # Achou um sinônimo desse key, não precisa testar outros sinônimos do mesmo key

        return best_key

    def _find_objeto(self, text: str) -> Optional[str]:
        best_key = None
        best_pos = None

        for obj_key, patterns in self._objetos_regex.items():
            for pat in patterns:
                m = pat.search(text)
                if m:
                    pos = m.start()
                    if best_pos is None or pos < best_pos:
                        best_pos = pos
                        best_key = obj_key
                    break
        return best_key

    def parse(self, texto_stt: str) -> ParseResult:
        text = normalizar(texto_stt)

        # 1) Só entra no modo comando se tiver a palavra "comando" como termo inteiro
        if not _regex_frase(self.keyword_command).search(text):
            return ParseResult(is_command=False, reason="Sem palavra-chave 'comando' (fallback).")

        # 2) Remove wake word e a palavra comando (como termos inteiros)
        if self.wake_word:
            text = _regex_frase(self.wake_word).sub(" ", text)
        text = _regex_frase(self.keyword_command).sub(" ", text)
        text = " ".join(text.split())

        # 3) Extrai ação/objeto/local
        acao = self._find_first_match_key(text, self._acoes_regex)
        if not acao:
            return ParseResult(is_command=True, reason="Ação não reconhecida")

        objeto = self._find_objeto(text)
        if not objeto:
            return ParseResult(is_command=True, reason="Objeto não reconhecido")

        local = self._find_first_match_key(text, self._locais_regex)
        rules = self.objetos[objeto]

        # 4) Regra local
        if rules["requer_local"] and not local:
            if rules["permitir_geral"] and "geral" in self.locais:
                local = "geral"
            else:
                return ParseResult(is_command=True, reason=f"Faltou local para '{objeto}'.")

        if not local and "geral" in self.locais:
            local = "geral"

        # 5) Comando final
        comando = f"Tudo bem! Executando {acao} {objeto} - {local}".upper()

        return ParseResult(
            is_command=True,
            reason="OK",
            acao=acao,
            objeto=objeto,
            local=local,
            comando_para_cliente=comando
        )



# # Teste p validar código parser 
# if __name__ == "__main__":
#     parser = CommandParser("comandos_base.json", wake_word="Aurelius")

#     exemplos = [
#         "Aurelius comando ligar a luz do quarto",
#         "Aurelius comando desligar tv",
#         "Aurelius comando abrir portão",
#         "Aurelius comando acender iluminação da sala",
#         "Aurelius comando ligar luz",  # se luz requer local, vai virar geral se permitir_geral=True
#         "Aurelius comando ligar ar condicionado"  # deve pedir local
#     ]

#     for e in exemplos:
#         r = parser.parse(e)
#         print(e)
#         print(r)
#         print("-" * 60)
