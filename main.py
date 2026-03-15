import asyncio
import json
import pandas as pd
import websockets
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ==========================================
# 1. CONFIGURAÇÕES GERAIS
# ==========================================
APP_ID = "121512"
DERIV_WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"

estado_bot = {
    "token_deriv": None,
    "modo_automatico": False,
    "ativo": "R_10",
    "stake": 10.0,
    "take_profit": 50.0,
    "stop_loss": 20.0,
    "lucro_diario": 0.0,
    "velas": [],
    "order_block": None,
    "ultima_vela_narrada": 0  # Evita o bot flodar a tela de mensagens
}

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])
conexoes_html = []

# ==========================================
# 2. SERVINDO O SEU SITE (HTML) PELO RENDER
# ==========================================
@app.get("/")
async def pagina_inicial():
    caminho_html = os.path.join(os.path.dirname(__file__), "index.html")
    with open(caminho_html, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200)

# ==========================================
# 3. COMUNICAÇÃO COM O CELULAR (HTML)
# ==========================================
async def enviar_log_html(mensagem, fonte="PYTHON", cor="text-gray-300"):
    pacote = json.dumps({"tipo": "log", "fonte": fonte, "mensagem": mensagem, "cor": cor})
    for conexao in conexoes_html:
        try: await conexao.send_text(pacote)
        except: pass

async def enviar_saldo(saldo):
    pacote = json.dumps({"tipo": "saldo", "valor": f"{saldo:.2f}"})
    for conexao in conexoes_html:
        try: await conexao.send_text(pacote)
        except: pass

@app.websocket("/painel")
async def websocket_painel(websocket: WebSocket):
    await websocket.accept()
    conexoes_html.append(websocket)
    
    try:
        while True:
            dados = await websocket.receive_text()
            comando = json.loads(dados)
            
            if comando["tipo"] == "iniciar":
                estado_bot["token_deriv"] = comando["token"]
                estado_bot["ativo"] = comando["ativo"]
                await enviar_log_html(f"Motores ligados no ativo {estado_bot['ativo']}...", cor="text-blue-400")
                asyncio.create_task(motor_deriv_ws())
                
            elif comando["tipo"] == "toggle_auto":
                estado_bot["modo_automatico"] = comando["status"]
                
            elif comando["tipo"] == "configs":
                if estado_bot["ativo"] != comando["ativo"]:
                    estado_bot["ativo"] = comando["ativo"]
                    estado_bot["velas"] = [] 
                    estado_bot["order_block"] = None
                    await enviar_log_html(f"Trocando radar para {estado_bot['ativo']}...", cor="text-yellow-400")
                
                estado_bot["stake"] = comando["stake"]
                estado_bot["take_profit"] = comando["tp"]
                estado_bot["stop_loss"] = comando["sl"]
                await enviar_log_html("Gerenciamento de Risco Atualizado e Travado.", cor="text-purple-400")

    except WebSocketDisconnect:
        conexoes_html.remove(websocket)

# ==========================================
# 4. MOTOR WEBSOCKET DA DERIV
# ==========================================
async def motor_deriv_ws():
    async with websockets.connect(DERIV_WS_URL) as ws_deriv:
        # Autorização
        await ws_deriv.send(json.dumps({"authorize": estado_bot["token_deriv"]}))
        resp_auth = json.loads(await ws_deriv.recv())
        
        if "error" in resp_auth:
            await enviar_log_html(f"Erro Token: {resp_auth['error']['message']}", cor="text-red-500")
            return
            
        # Pede Inscrição de Saldo Ao Vivo
        await ws_deriv.send(json.dumps({"balance": 1, "subscribe": 1}))
        
        # Pede 1000 velas de 1 minuto
        await ws_deriv.send(json.dumps({
            "ticks_history": estado_bot["ativo"], "adjust_start_time": 1,
            "count": 1000, "end": "latest", "style": "candles",
            "granularity": 60, "subscribe": 1
        }))

        while True:
            # Se a memória esvaziar (troca de ativo)
            if not estado_bot["velas"]:
                await ws_deriv.send(json.dumps({
                    "ticks_history": estado_bot["ativo"], "adjust_start_time": 1,
                    "count": 1000, "end": "latest", "style": "candles",
                    "granularity": 60, "subscribe": 1
                }))
            
            mensagem = json.loads(await ws_deriv.recv())
            
            # Atualiza o Saldo na Tela
            if "balance" in mensagem:
                await enviar_saldo(mensagem["balance"]["balance"])
            
            # Histórico Inicial
            elif "candles" in mensagem:
                estado_bot["velas"] = mensagem["candles"]
                await enviar_log_html("1.000 velas processadas. Iniciando narração do mercado...", cor="text-blue-400")
                
            # Atualização das Velas Ao Vivo
            elif "ohlc" in mensagem:
                vela = mensagem["ohlc"]
                if estado_bot["velas"] and estado_bot["velas"][-1]["epoch"] == int(vela["open_time"]):
                    estado_bot["velas"][-1] = { "epoch": int(vela["open_time"]), "open": float(vela["open"]), "high": float(vela["high"]), "low": float(vela["low"]), "close": float(vela["close"]) }
                else:
                    estado_bot["velas"].append({ "epoch": int(vela["open_time"]), "open": float(vela["open"]), "high": float(vela["high"]), "low": float(vela["low"]), "close": float(vela["close"]) })
                    if len(estado_bot["velas"]) > 1000: estado_bot["velas"].pop(0)

                await analisar_mercado_avancado(ws_deriv)

# ==========================================
# 5. CÉREBRO E NARRADOR (SMC + LARRY)
# ==========================================
async def analisar_mercado_avancado(ws_deriv):
    if len(estado_bot["velas"]) < 100: return
        
    df = pd.DataFrame(estado_bot["velas"])
    preco_atual = df.iloc[-1]['close']
    tempo_atual = df.iloc[-1]['epoch']
    
    # Indicadores
    df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    ema_12 = df['close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema_12 - ema_26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()

    vela_atual = df.iloc[-1]
    vela_ant  = df.iloc[-2]
    vela_ant2 = df.iloc[-3]
    
    nova_vela_fechou = tempo_atual != estado_bot["ultima_vela_narrada"]
    
    # SMC: Procurando OB se não tiver nenhum
    if estado_bot["order_block"] is None:
        for i in range(len(df)-10, 50, -1):
            if df['close'].iloc[i] > df['open'].iloc[i] and df['close'].iloc[i-1] > df['open'].iloc[i-1]:
                if df['close'].iloc[i-2] < df['open'].iloc[i-2]:
                    estado_bot["order_block"] = { "tipo": "BULLISH", "maxima": df['high'].iloc[i-2], "minima": df['low'].iloc[i-2] }
                    await enviar_log_html(f"📍 SMC: Order Block Mapeado. Região: {estado_bot['order_block']['minima']:.2f} a {estado_bot['order_block']['maxima']:.2f}.", cor="text-yellow-400 font-bold")
                    break
        
        if nova_vela_fechou and estado_bot["order_block"] is None:
            await enviar_log_html(f"📊 Preço: {preco_atual:.2f} | Buscando regiões de banco (OB)... Nenhum volume institucional detectado ainda.", cor="text-gray-500")

    # SE O OB ESTÁ MAPEADO: A NARRAÇÃO DO ATAQUE
    if estado_bot["order_block"]:
        ob = estado_bot["order_block"]
        
        # 1. O Preço está Longe
        if preco_atual > (ob["maxima"] * 1.002):
            if nova_vela_fechou:
                await enviar_log_html(f"👀 Observando o mercado... Preço ({preco_atual:.2f}) ainda está longe da Zona de Tiro ({ob['maxima']:.2f}).", cor="text-gray-400")
                
        # 2. O Preço ENTROU na Zona de Tiro! (Alerte a cada Tick)
        elif ob["minima"] <= preco_atual <= (ob["maxima"] * 1.002):
            await enviar_log_html(f"⚠️ ALERTA: O Preço entrou na Zona do Banco (Order Block)! Verificando exaustão (RSI)...", cor="text-yellow-400 font-bold")
            
            # 3. Verifica RSI e MACD
            if vela_atual['RSI'] < 40 and vela_atual['MACD'] > vela_atual['Signal']:
                await enviar_log_html(f"⏳ Exaustão confirmada (RSI {vela_atual['RSI']:.0f}). Armando Gatilho Larry Williams 9.1...", cor="text-blue-400")
                
                # 4. Gatilho do Tiro
                if vela_ant2['EMA_9'] > vela_ant['EMA_9'] and vela_atual['EMA_9'] > vela_ant['EMA_9']:
                    gatilho_compra = vela_ant['high']
                    
                    if preco_atual > gatilho_compra:
                        await enviar_log_html(f"🔥 MATEMÁTICA PERFEITA! Executando CALL (Compra).", cor="text-green-400 font-black text-sm")
                        await executar_ordem(ws_deriv, "CALL")
                        estado_bot["order_block"] = None
            else:
                # Dentro do OB, mas indicadores fracos
                if nova_vela_fechou:
                    await enviar_log_html(f"⛔ Preço na zona, mas força vendedora ainda alta (RSI: {vela_atual['RSI']:.0f}). Protegendo capital. Aguardando virada...", cor="text-red-400")

    estado_bot["ultima_vela_narrada"] = tempo_atual

# ==========================================
# 6. EXECUÇÃO E GERENCIAMENTO DE RISCO
# ==========================================
async def executar_ordem(ws_deriv, direcao):
    if estado_bot["lucro_diario"] >= estado_bot["take_profit"]:
        await enviar_log_html(f"🏆 META BATIDA! Lucro: +${estado_bot['lucro_diario']:.2f}. Robô dormindo.", cor="text-green-500 font-black")
        return
    if estado_bot["lucro_diario"] <= -estado_bot["stop_loss"]:
        await enviar_log_html(f"🩸 LIMITE DE PERDA ATINGIDO (-${abs(estado_bot['lucro_diario']):.2f}). Segurança ativada.", cor="text-red-500 font-black")
        return

    if not estado_bot["modo_automatico"]:
        await enviar_log_html(f"Sinal de {direcao} abortado -> MODO AUTOMÁTICO DESLIGADO.", cor="text-gray-500")
        await asyncio.sleep(10)
        return
        
    await enviar_log_html(f"💸 COMPRANDO: {direcao} a ${estado_bot['stake']}!", cor="text-green-500 font-bold")
    
    ordem = {
        "buy": 1, "price": estado_bot["stake"],
        "parameters": {
            "amount": estado_bot["stake"], "basis": "stake", "contract_type": direcao,
            "currency": "USD", "duration": 1, "duration_unit": "m", "symbol": estado_bot["ativo"]
        }
    }
    await ws_deriv.send(json.dumps(ordem))
    await asyncio.sleep(60)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
