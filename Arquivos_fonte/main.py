# Imports do Vosk
import sounddevice as sd
import queue
import json
from vosk import Model, KaldiRecognizer
# Imports do MQTT
from llama_cpp import Llama
import paho.mqtt.client as mqtt
# Imports da GUI
from PyQt5 import QtCore, QtWidgets, uic
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtCore import QTimer, QDateTime
from PyQt5.QtWidgets import QMessageBox
import sys
import locale
import resourceQt
# Import Arquivo Parser
from parser_json import CommandParser
# Imports do TTS 
import subprocess
import numpy as np
import time
import threading


# Váriavel global para troca de informações entre Threads e Assistente ouvindo
global ui
ouvindo_continuo = False
global NOME_ASSISTENTE

# Evento rodando em thread que checa se tts está falando para stt não ouvir
tts_falando = threading.Event()

# Arquivo do dicionário JSON de clientes
with open('Arquivos_JSON/clientes_mqtt.json', 'r', encoding='UTF-8') as f:
    clientes_mqtt = json.load(f)


# Configurações do MQTT
MQTT_BROKER = "192.168.0.100"  # Aqui, coloque o IP do seu Broker
MQTT_PORT = 1883
TOPICO_ENTRADA = "assistente/voz"       
TOPICO_COMANDO = "assistente/comando"  
TOPICO_RESPOSTA = "assistente/resposta" 

# Configurações do Vosk
# Caminho do seu modelo Vosk
VOSK_MODEL_PATH = r"/home/admin/Desktop/modelo_vosk/vosk-model-pt-fb-v0.1.1-20220516_2113"
SAMPLERATE = 16000

# Caminho do seu modelo LLM
LLM_MODEL_PATH = r"/home/admin/Desktop/modelos_llm/gemma-portuguese-luana-2b.Q4_K_M.gguf"

# Nome Inicial da assistente
NOME_ASSISTENTE = "Assistente"

# Arquivo do dicionário JSON de comandos
COMANDOS_JSON_PATH = "Arquivos_JSON/comandos_base.json"

# Funçoẽs para carregar comandos novamente e salvá-lo após alterá-lo
def carregar_comandos(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
    

def salvar_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# MQTT
client = mqtt.Client()
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.loop_start()
client.reconnect_delay_set(1,5)

# Vosk
q_audio = queue.Queue()
vosk_model = Model(VOSK_MODEL_PATH)
recognizer = KaldiRecognizer(vosk_model, SAMPLERATE)

# Reduz consumo de CPU, so retorna texto final do recognizer
recognizer.SetWords(False)

# Função q chama o TTS
def falar(texto):
    # Configurações do TTS (Verificar onde foi instalado o eSpeak na máquina)
    tts_falando.set()
    subprocess.run([
        "espeak-ng",
        "-v", "mb-br3",   
        "-s", "140",
        "-p", "40",
        texto
    ])
    time.sleep(0.5)
    tts_falando.clear()

# Declaração da classe Llama
llm = Llama(
    model_path=LLM_MODEL_PATH,
    n_ctx=1024,
    n_threads=4,
    use_mlock=False,
    verbose=False
)

# Parser
parser = CommandParser(COMANDOS_JSON_PATH, wake_word=NOME_ASSISTENTE)

# Função para ver se o nome foi ativo
def foi_ativado(texto):
    return NOME_ASSISTENTE.lower() in texto.lower()

def extrair_comando(texto):
    texto_lower = texto.lower()
    nome = NOME_ASSISTENTE.lower()

    if nome in texto_lower:
        partes = texto_lower.split(nome, 1)
        comando = partes[1].strip()
        return comando

    return texto

# Função para responder com LLM
def responder_com_llm(texto_usuario: str) -> str:
    prompt = f"""Seu nome é {NOME_ASSISTENTE}, uma assistente virtual offline.
    Responda em português do Brasil, de forma curta e direta.
    Se o usuário pedir um comando de controlar alguma coisa (Exemplo: "Ligar luz"), diga: "Para comandos, diga "comando" antes de sua ação."
    Não invente. Se não souber, diga: "Desculpe, não sei responder isso."
    Pergunta: {texto_usuario}
    Resposta:"""
    resp = llm(
        prompt,
        max_tokens=60,
        temperature=0.05,
        top_p=0.70,
        repeat_penalty=1.25,
        stop=["Pergunta:", "Resposta:", "Usuário:"]
         )
    
    return resp["choices"][0]["text"].strip()


# Loop de voz para reconhecimento
def audio_callback(indata, frames, time_info, status):
    """Callback correto para Vosk: bytes crus."""
    # Verifica se o tts esta falando para ignorar reconhecimento e limpar buffer do audio (q_audio)
    if tts_falando.is_set():
        while not q_audio.empty():  #Enquanto não está vazia, limpa a Queue de audio
            q_audio.get_nowait()
        return
    q_audio.put(bytes(indata))

def loop_voz():
    print(f" {NOME_ASSISTENTE} iniciada. Para comandos, diga: 'Comando ...' (Ctrl+C para sair)\n")
    
    with sd.RawInputStream(
        samplerate=SAMPLERATE,
        blocksize=4000,
        dtype="int16",
        channels= 1,
        callback=audio_callback
    ):
        while True:
            data = q_audio.get()
            
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                texto = result.get("text", "").strip()
                
                

                if not texto or len(texto.split()) < 2:  # Filtro para palavras curtas ou ruído
                    continue

                # Print da conversa
                print(f" Usuário: {texto}")
                client.publish(TOPICO_ENTRADA, texto)  
                ui.sinal_usuario.emit(texto)
                
                # Ativação com o nome da llm
                if not ouvindo_continuo and not foi_ativado(texto):
                    ui.sinal_resposta.emit(f"Ignorado - diga o nome {NOME_ASSISTENTE}")

                    print(" Ignorado (sem ativação)\n")
                    continue

                #  Limpar texto que vem antes do nome do assistente
                if not ouvindo_continuo:
                    texto = extrair_comando(texto)
                # Parser decide se é comando
                r = parser.parse(texto)

                # Se for comando e reconheceu
                if r.is_command and r.comando_para_cliente:
                    # Print do comando
                    print(f" CMD: {r.comando_para_cliente}\n")
                    print(f"ação: {r.acao}")
                    print(f"objeto: {r.objeto}")
                    print(f"local: {r.local}")
                    acao = str(r.acao)
                    objeto = str(r.objeto)
                    local = str(r.local)
                    json_final_comando = { f"acao": acao,
                                        "objeto": objeto,
                                        "local": local } 
                    client.publish(TOPICO_COMANDO, json.dumps(json_final_comando, ensure_ascii=False))
                    ui.sinal_resposta.emit(f"Tudo bem, executando comando {acao} {objeto} {local}!")
                    falar(f"Tudo bem, executando comando {acao} {objeto} {local}!")

                # Se era modo comando, mas não reconheceu
                elif r.is_command and not r.comando_para_cliente:
                    print(f" CMD ERRO: {r.reason}\n")
                    client.publish(TOPICO_RESPOSTA, f"Comando não reconhecido: {r.reason}")
                    ui.sinal_resposta.emit(f"Comando não reconhecido: {r.reason}!")
                    falar(f"Comando não reconhecido: {r.reason}!")
                # Se não era modo comando → conversa
                else:
                    resposta = responder_com_llm(texto)

                    print(f" {NOME_ASSISTENTE}: {resposta}\n")
                    client.publish(TOPICO_RESPOSTA, resposta)
                    ui.sinal_resposta.emit(resposta)
                    falar(resposta)

# INÍCIO DO MÓDULO DA GUI

#OBEJETO DE PERSONALIZAR TELA PERSONALIZAR  (TIPO DIALOG) 
class PersonalizarDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        uic.loadUi("Telas/tela_personalizacao.ui", self) 
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.atualizar_hora)
        self.timer.start(1000)
        self.setWindowTitle("Personalizar Assistente")
        self.atualizar_hora()
        if self.parent().DARK_MODE == 1:
            self.fundo.setPixmap(QPixmap("Imagens_PyQT/fundo_darkmode.png"))
        else:
            self.fundo.setPixmap(QPixmap("Imagens_PyQT/fundo_sem nada.png"))

        self.botao_retornar.clicked.connect(self.close)   #Botão para voltar a tela incial
        self.pushButton_cancelar.clicked.connect(self.close)
        self.pushButton_salvar.clicked.connect(self.salvar_gpio)
        self.pushButton_add_item.clicked.connect(self.abrir_add_item)
        self.pushButton_ligar_io.clicked.connect(self.comando_manual_ligar_gpio)
        self.pushButton_desligar_io.clicked.connect(self.comando_manual_desligar_gpio)

        self.atualizar_combobox_micro()

        #Ao mudar, roda função de change
        self.comboBox_sel_micro.currentIndexChanged.connect(self._on_micro_change)
        #Rodar a primeira vez o change para atribuir os IOs assim que entrar na tela
        self._on_micro_change()

        #Atualiza combobox objeto e local
        comandos = carregar_comandos(COMANDOS_JSON_PATH)

        self.comboBox_objeto.addItems(comandos["objetos"])
        self.comboBox_local.addItems(comandos["locais"])
    
    def comando_manual_ligar_gpio(self):
        json_comando = { f"acao": "ligar",
        "objeto": self.comboBox_objeto.currentText(),
        "local": self.comboBox_local.currentText() } 
        client.publish(TOPICO_COMANDO, json.dumps(json_comando, ensure_ascii=False))

    def comando_manual_desligar_gpio(self):
        json_comando = { f"acao": "desligar",
        "objeto": self.comboBox_objeto.currentText(),
        "local": self.comboBox_local.currentText() } 
        client.publish(TOPICO_COMANDO, json.dumps(json_comando, ensure_ascii=False))


    def salvar_gpio(self):
        cliente_mqtt_atual = clientes_mqtt[self.comboBox_sel_micro.currentText()]["id"]
        TOPICO_CLIENTE = f"assistente/{cliente_mqtt_atual}/config_io"
        num_gpio = int(self.comboBox_sel_IO.currentText())
        nome_objeto = self.comboBox_objeto.currentText()
        nome_local = self.comboBox_local.currentText()
        dados_salvos = { "gpio": num_gpio,
                         "objeto": nome_objeto,
                         "local": nome_local}
        client.publish(TOPICO_CLIENTE, json.dumps(dados_salvos, ensure_ascii=False))  #JSON.DUMPS FORÇA JSON STRING
        QMessageBox.warning(self, "Config. GPIO", f"Configurações enviadas para o cliente com sucesso.")

    def atualizar_hora(self):
        agora = QDateTime.currentDateTime()
        hora = agora.toString("HH:mm")
        self.texto_hora.setText(hora)
    
    def atualizar_combobox_micro(self):
        self.comboBox_sel_micro.clear()
        self.comboBox_sel_micro.addItems(list(clientes_mqtt.keys()))

    def _on_micro_change(self):
        micro_atual = self.comboBox_sel_micro.currentText()
        if micro_atual not in clientes_mqtt:
            return
        
        dados_micro_atual = clientes_mqtt[micro_atual]

        icone_path = dados_micro_atual["caminho_icone"]
        print(icone_path)
        self.icone_micro.setIcon(QIcon(dados_micro_atual["caminho_icone"]))
        self.icone_micro.setIconSize(self.icone_micro.size())  # Ocupa o botão inteiro
        self.nome_micro.setText(dados_micro_atual["tipo_micro"])

        self.comboBox_sel_IO.clear()
        self.comboBox_sel_IO.addItems(dados_micro_atual["ios"])        

    def abrir_add_item(self):
        dlg = AddItemDialog(self)
        dlg.exec_() 
        if dlg.exec_() == QtWidgets.QDialog.close:
        # Atualiza os comboboxes da tela de personalizar sem precisar reiniciar
            comandos = carregar_comandos(COMANDOS_JSON_PATH)
            self.comboBox_objeto.clear()
            self.comboBox_objeto.addItems(comandos["objetos"])
            self.comboBox_local.clear()
            self.comboBox_local.addItems(comandos["locais"])


class AddItemDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        uic.loadUi("Telas/adicao_de_itens.ui", self) 
        self.comboBox_sel_item.addItems(["Objeto", "Local"])
        self.pushButton_cancelar.clicked.connect(self.close) 
        self.pushButton_salvar.clicked.connect(self.on_salvar)
        self.comboBox_sel_item.currentIndexChanged.connect(self.on_tipo_change)

        self.on_tipo_change()

    def on_tipo_change(self):
        self.texto_item.setText(self.comboBox_sel_item.currentText())
    def on_salvar(self):
        tipo = self.comboBox_sel_item.currentText().strip().lower()  # Para virar "objeto" ou "local"
        nome = self.textEdit_item.toPlainText().strip()

        if not nome:
            QMessageBox.warning(self, "Atenção", "Digite um nome.")
            return

        # Normalização simples para nome_formatado (sem espaços, sem acento é opcional)
        nome_formatado = nome.lower().strip().replace(" ", "_")

        # Não permite informar sinônimos, usa o nome como sinônimo
        sinonimos = [nome]

        data = carregar_comandos(COMANDOS_JSON_PATH)

        if tipo == "objeto":
            objetos = data.get("objetos", {})  

            if nome_formatado in objetos:
                QMessageBox.warning(self, "Já existe", f"O objeto '{nome_formatado}' já existe no JSON.")
                return

            # Regra padrão (você pode depois deixar editável)
            objetos[nome_formatado] = {
                "sinonimos": sinonimos,
                "requer_local": True,
                "permitir_geral": True
            }

            data["objetos"] = objetos

        elif tipo == "local":
            locais = data.get("locais", {})

            if nome_formatado in locais:
                QMessageBox.warning(self, "Já existe", f"O local '{nome_formatado}' já existe no JSON.")
                return

            locais[nome_formatado] = sinonimos  # locais são lista de sinônimos
            data["locais"] = locais
            

        else:
            QMessageBox.critical(self, "Erro", "Tipo inválido.")
            return

        salvar_json(COMANDOS_JSON_PATH, data)

        # Guarda um "resultado" para a tela principal atualizar os combos
        self.resultado = {"tipo": tipo, "nome_formatado": nome_formatado, "sinonimos": sinonimos}

        QMessageBox.information(self, "Sucesso", f"{tipo.title()} '{nome_formatado}' adicionado com sucesso.")

        self.accept()  # Fecha a janela ao salvar (aceito)

class AlterarNomeDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        uic.loadUi("Telas/alterar_nome_assistente.ui", self) 
        self.pushButton_cancelar.clicked.connect(self.close) 
        self.pushButton_salvar.clicked.connect(self.atualizar_nome_assistente)
    def atualizar_nome_assistente(self):

        novo_nome = self.textEdit_nome_assistente.toPlainText().strip()

        if not novo_nome:
            return

        global NOME_ASSISTENTE
        NOME_ASSISTENTE = novo_nome.capitalize()

        # Atualiza parser
        parser.wake_word = NOME_ASSISTENTE

        # Atualiza a janela principal
        if self.parent():
            self.parent().setWindowTitle(f"Assistente Virtual {NOME_ASSISTENTE}")
            self.parent().nome_assistente.setText(NOME_ASSISTENTE)
            self.parent().pushButton_ouvindo_parado.setText(f"{NOME_ASSISTENTE}")
            self.parent().adicionar_chat("Sistema", f"Nome alterado para {NOME_ASSISTENTE}")

        QMessageBox.information(self, "Sucesso", f"Nome da assistente alterado para {NOME_ASSISTENTE}.")

        self.accept()  # Fecha a janela ao salvar (aceito)



class AssistenteUI(QtWidgets.QMainWindow):
    #Receber sinais em do texto do usuário e da resposta
    sinal_usuario = QtCore.pyqtSignal(str)
    sinal_resposta = QtCore.pyqtSignal(str)
    def __init__(self):
        super().__init__()
        uic.loadUi("Telas/interface_grafica.ui", self)
        self.setWindowTitle(f"Assistente Virtual {NOME_ASSISTENTE}")
        self.DARK_MODE = 0
        self.botao_darkmode.setIcon(QIcon("Imagens_PyQT/interruptor_darkmode_off.png"))
        self.nome_assistente.setText(f"{NOME_ASSISTENTE}")

        self.pushButton_ouvir.clicked.connect(self.iniciar_escuta)
        self.pushButton_parar.clicked.connect(self.parar_escuta)
        self.pushButton_parar.setEnabled(False)

        self.pushButton_ouvindo_parado.setFlat(True)
        self.pushButton_ouvindo_parado.setStyleSheet("background: transparent; color: rgb(230, 230, 0)")
        self.pushButton_ouvindo_parado.setEnabled(True)
        self.pushButton_ouvindo_parado.clicked.connect(self.abrir_alterar_nome)

        #Receber sinais em do texto do usuário e da resposta
        self.sinal_usuario.connect(self.mostrar_usuario)
        self.sinal_resposta.connect(self.mostrar_resposta)

        #Hora
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.atualizar_data_hora)
        self.timer.start(1000)
        self.atualizar_data_hora()

        #linkar botão para abrir tela personalizar
        self.botao_personalizar.clicked.connect(self.abrir_personalizar)
        self.botao_darkmode.clicked.connect(self.darkmode)

    def atualizar_data_hora(self):
        agora = QDateTime.currentDateTime()
        hora = agora.toString("HH:mm")
        meses = [ "janeiro", "fevereiro", "março", "abril",
                  "maio", "junho", "julho", "agosto",
                  "setembro", "outubro", "novembro", "dezembro"]
        
        dia  = agora.date().day()
        mes  = meses[agora.date().month() - 1]

        
        hora_int = agora.time().hour()

        if 7 <= hora_int < 12:
            saudacao = "Bom dia"
        elif 12 <= hora_int < 18:
            saudacao = "Boa tarde"
        else:
            saudacao = "Boa noite"
        # Mudando texto de data, hora e saudação
        self.texto_hora.setText(hora)
        self.texto_data.setText(f"{dia} de {mes}")
        self.texto_saudacao.setText(saudacao)

    def abrir_personalizar(self):
        dlg = PersonalizarDialog(self)
        dlg.exec_()  # modal (trava a principal até fechar)

    def abrir_alterar_nome(self):
        dlg = AlterarNomeDialog(self)
        dlg.exec_()  # modal (trava a principal até fechar)

    def darkmode(self):
        if self.DARK_MODE == 0:
            self.fundo.setPixmap(QPixmap("Imagens_PyQT/fundo_darkmode.png"))
            self.botao_darkmode.setIcon(QIcon("Imagens_PyQT/interruptor_darkmode_on.png"))
            self.texto_saudacao.setStyleSheet("background: transparent;\ncolor: rgb(240, 240, 240);")  
            self.DARK_MODE = 1
        else:
            self.fundo.setPixmap(QPixmap("Imagens_PyQT/fundo_sem nada.png"))
            self.botao_darkmode.setIcon(QIcon("Imagens_PyQT/interruptor_darkmode_off.png"))
            self.texto_saudacao.setStyleSheet("background: transparent;\ncolor: rgb(0, 0, 0);")  
            self.DARK_MODE = 0
        
    def adicionar_chat(self, autor, texto):
        #HTML para mudar a cor dos textos do Chat se quiser
        self.textEdit_Chat.append(
            f'<span style="color:#FFFFFF; font-weight:600; font-weight: bold;">{autor}:</span> '
            f'<span style="color:#FFFFFF;">{texto}</span>')
        
    #Ativa a escuyta continua e destiva o botão de ouvir
    def iniciar_escuta(self):
        global ouvindo_continuo
        ouvindo_continuo = True
        
        self.pushButton_ouvir.setFlat(True)
        self.pushButton_ouvir.setStyleSheet("background-color: transparent; color: gray; border: none")
        self.pushButton_ouvir.setEnabled(False)
        self.pushButton_ouvindo_parado.setFlat(True)
        self.pushButton_ouvindo_parado.setText("Ouvindo")
        self.pushButton_ouvindo_parado.setStyleSheet("background-color: None;\ncolor: rgb(0, 226, 109); border: none")
        self.pushButton_ouvindo_parado.setEnabled(False)
        self.pushButton_parar.setEnabled(True)
        self.pushButton_parar.setFlat(True)
        self.pushButton_parar.setStyleSheet("background: transparent;\ncolor: rgb(255, 255, 255); border: none")

        self.adicionar_chat("Sistema", "Modo escuta contínua ativado")

    def parar_escuta(self):
        global ouvindo_continuo
        ouvindo_continuo = False
        
        self.pushButton_ouvir.setFlat(True)
        self.pushButton_ouvir.setStyleSheet("background: transparent;\ncolor: rgb(255, 255, 255); border: none")
        self.pushButton_ouvir.setEnabled(True)
        self.pushButton_parar.setFlat(True)
        self.pushButton_parar.setStyleSheet("background: transparent;\ncolor: gray; border: none")
        self.pushButton_ouvindo_parado.setFlat(True)
        self.pushButton_ouvindo_parado.setText(f"{NOME_ASSISTENTE}")
        self.pushButton_ouvindo_parado.setStyleSheet("background: transparent;\ncolor: rgb(230, 230, 0); border: none")
        self.pushButton_ouvindo_parado.setEnabled(True)
        self.pushButton_parar.setEnabled(False)


        self.adicionar_chat("Sistema", "Modo escuta contínua desativado")

    def mostrar_usuario(self, texto):
        self.adicionar_chat("Você", texto)
    def mostrar_resposta(self, texto):
        self.adicionar_chat(NOME_ASSISTENTE, texto)


if __name__ == "__main__":
    import threading

    app = QtWidgets.QApplication(sys.argv)
    janela = AssistenteUI()
    janela.show()

    ui = janela


    t = threading.Thread(target=loop_voz, daemon=True)
    t.start()

    sys.exit(app.exec_())
