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
    
    # ESTATÍSTICAS E TRAVA
    "em_operacao": False, # Trava de segurança contra tiros duplos
    "lucro_diario": 0.0,
    "wins": 0,
    "losses": 0,
    
    "velas": [],
    "order_block": None,
    "ultima_vela_narrada": 0
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

async def enviar_estatisticas():
    pacote = {
        "tipo": "stats",
        "liquido": f"{estado_bot['lucro_diario']:.2f}",
        "wins": estado_bot["wins"],
        "loss": estado_bot["losses"]
    }
    for conexao in conexoes_html:
        try: await conexao.send_text(json.dumps(pacote))
        except: pass

@app.websocket("/painel")
async def websocket_painel(websocket: WebSocket):
    await websocket.accept()
    conexoes_html.append(websocket)
    
    await enviar_estatisticas()
    
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
                if estado_bot["modo_automatico"]:
                    await enviar_log_html("🟢 MODO AUTOMÁTICO ATIVADO. Bot operando na conta.", cor="text-green-400 font-bold")
                else:
                    await enviar_log_html("⏸️ MODO AUTOMÁTICO DESLIGADO. Apenas gerando sinais.", cor="text-yellow-400")
                
            elif comando["tipo"] == "configs":
                if estado_bot["ativo"] != comando["ativo"]:
                    estado_bot["ativo"] = comando["ativo"]
                    estado_bot["velas"] = [] 
                    estado_bot["order_block"] = None
                    await enviar_log_html(f"Trocando radar para {estado_bot['ativo']}...", cor="text-yellow-400")
                
                estado_bot["stake"] = comando["stake"]
                estado_bot["take_profit"] = comando["tp"]
                estado_bot["stop_loss"] = comando["sl"]
                await enviar_log_html(f"Risco Configurado: Stake ${estado_bot['stake']} | TP ${estado_bot['take_profit']} | SL ${estado_bot['stop_loss']}", cor="text-purple-400")

    except WebSocketDisconnect:
        conexoes_html.remove(websocket)

# ==========================================
# 4. MOTOR WEBSOCKET DA DERIV
# ==========================================
async def motor_deriv_ws():
    async with websockets.connect(DERIV_WS_URL) as ws_deriv:
        await ws_deriv.send(json.dumps({"authorize": estado_bot["token_deriv"]}))
        resp_auth = json.loads(await ws_deriv.recv())
        
        if "error" in resp_auth:
            await enviar_log_html(f"Erro Token: {resp_auth['error']['message']}", cor="text-red-500")
            return
            
        await ws_deriv.send(json.dumps({"balance": 1, "subscribe": 1}))
        await ws_deriv.send(json.dumps({
            "ticks_history": estado_bot["ativo"], "adjust_start_time": 1,
            "count": 1000, "end": "latest", "style": "candles",
            "granularity": 60, "subscribe": 1
        }))

        while True:
            if not estado_bot["velas"]:
                await ws_deriv.send(json.dumps({
                    "ticks_history": estado_bot["ativo"], "adjust_start_time": 1,
                    "count": 1000, "end": "latest", "style": "candles",
                    "granularity": 60, "subscribe": 1
                }))
            
            mensagem = json.loads(await ws_deriv.recv())
            
            if "balance" in mensagem:
                await enviar_saldo(mensagem["balance"]["balance"])
            
            elif "candles" in mensagem:
                estado_bot["velas"] = mensagem["candles"]
                await enviar_log_html("1.000 velas processadas. Iniciando análise de mercado...", cor="text-blue-400")
                
            elif "ohlc" in mensagem:
                vela = mensagem["ohlc"]
                if estado_bot["velas"] and estado_bot["velas"][-1]["epoch"] == int(vela["open_time"]):
                    estado_bot["velas"][-1] = { "epoch": int(vela["open_time"]), "open": float(vela["open"]), "high": float(vela["high"]), "low": float(vela["low"]), "close": float(vela["close"]) }
                else:
                    estado_bot["velas"].append({ "epoch": int(vela["open_time"]), "open": float(vela["open"]), "high": float(vela["high"]), "low": float(vela["low"]), "close": float(vela["close"]) })
                    if len(estado_bot["velas"]) > 1000: estado_bot["velas"].pop(0)

                await analisar_mercado_avancado(ws_deriv)

            # RASTREANDO O RESULTADO DA OPERAÇÃO
            elif "buy" in mensagem:
                if "error" not in mensagem:
                    contract_id = mensagem["buy"]["contract_id"]
                    # Pede para a Deriv avisar quando esse contrato fechar
                    await ws_deriv.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
                else:
                    await enviar_log_html(f"Erro ao comprar: {mensagem['error']['message']}", cor="text-red-500")
                    estado_bot["em_operacao"] = False
                
            elif "proposal_open_contract" in mensagem:
                contrato = mensagem["proposal_open_contract"]
                if contrato:
                    # Se o contrato fechou (Ganhou ou Perdeu)
                    if contrato.get("is_sold") == 1:
                        lucro_final = float(contrato["profit"])
                        
                        if lucro_final > 0:
                            estado_bot["wins"] += 1
                            await enviar_log_html(f"✅ WIN! Lucro de ${lucro_final:.2f}", cor="text-green-400 font-bold")
                        else:
                            estado_bot["losses"] += 1
                            await enviar_log_html(f"❌ LOSS. Perda de ${abs(lucro_final):.2f}", cor="text-red-400 font-bold")
                            
                        estado_bot["lucro_diario"] += lucro_final
                        estado_bot["em_operacao"] = False # Destrava o robô para o próximo sinal
                        await enviar_estatisticas()

# ==========================================
# 5. CÉREBRO: LÓGICA E GATILHO
# ==========================================
async def analisar_mercado_avancado(ws_deriv):
    if len(estado_bot["velas"]) < 100: return
    
    # Se o robô estiver operando, ele não olha o gráfico para não atirar duas vezes
    if estado_bot["em_operacao"]: return
        
    df = pd.DataFrame(estado_bot["velas"])
    preco_atual = df.iloc[-1]['close']
    tempo_atual = df.iloc[-1]['epoch']
    
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
    
    if estado_bot["order_block"] is None:
        for i in range(len(df)-10, 50, -1):
            if df['close'].iloc[i] > df['open'].iloc[i] and df['close'].iloc[i-1] > df['open'].iloc[i-1]:
                if df['close'].iloc[i-2] < df['open'].iloc[i-2]:
                    estado_bot["order_block"] = { "tipo": "BULLISH", "maxima": df['high'].iloc[i-2], "minima": df['low'].iloc[i-2] }
                    await enviar_log_html(f"📍 SMC: Order Block Mapeado. Região: {estado_bot['order_block']['minima']:.2f} a {estado_bot['order_block']['maxima']:.2f}.", cor="text-yellow-400 font-bold")
                    break
        
        if nova_vela_fechou and estado_bot["order_block"] is None:
            await enviar_log_html(f"📊 Preço: {preco_atual:.2f} | Aguardando estrutura de mercado (OB)...", cor="text-gray-500")

    if estado_bot["order_block"]:
        ob = estado_bot["order_block"]
        if preco_atual > (ob["maxima"] * 1.002):
            if nova_vela_fechou:
                await enviar_log_html(f"👀 Observando... Preço ({preco_atual:.2f}) distante da Zona ({ob['maxima']:.2f}).", cor="text-gray-400")
                
        elif ob["minima"] <= preco_atual <= (ob["maxima"] * 1.002):
            await enviar_log_html(f"⚠️ ALERTA: O Preço entrou na Zona do Banco (Order Block)!", cor="text-yellow-400 font-bold")
            
            if vela_atual['RSI'] < 40 and vela_atual['MACD'] > vela_atual['Signal']:
                await enviar_log_html(f"⏳ Exaustão confirmada (RSI {vela_atual['RSI']:.0f}). Armando Gatilho...", cor="text-blue-400")
                
                if vela_ant2['EMA_9'] > vela_ant['EMA_9'] and vela_atual['EMA_9'] > vela_ant['EMA_9']:
                    gatilho_compra = vela_ant['high']
                    
                    if preco_atual > gatilho_compra:
                        await enviar_log_html(f"🔥 SINAL DETECTADO: CALL (Compra)!", cor="text-green-400 font-black text-sm")
                        await executar_ordem(ws_deriv, "CALL")
                        estado_bot["order_block"] = None
            else:
                if nova_vela_fechou:
                    await enviar_log_html(f"⛔ Preço na zona, mas indicadores ainda fracos. Protegendo capital...", cor="text-red-400")

    estado_bot["ultima_vela_narrada"] = tempo_atual

async def executar_ordem(ws_deriv, direcao):
    if estado_bot["lucro_diario"] >= estado_bot["take_profit"]:
        await enviar_log_html(f"🏆 META BATIDA! Bot pausado.", cor="text-green-500 font-black")
        return
    if estado_bot["lucro_diario"] <= -estado_bot["stop_loss"]:
        await enviar_log_html(f"🩸 LIMITE DE PERDA ATINGIDO. Segurança ativada.", cor="text-red-500 font-black")
        return

    # SINALIZADOR
    if not estado_bot["modo_automatico"]:
        await enviar_log_html(f"🔔 SINALIZADOR: Oportunidade de {direcao} exata agora! Faça a entrada manualmente na Deriv.", cor="text-yellow-400 font-black")
        estado_bot["order_block"] = None 
        return
        
    # MODO AUTOMÁTICO (Trava o robô e atira)
    estado_bot["em_operacao"] = True
    await enviar_log_html(f"💸 EXECUTANDO ORDEM: {direcao} a ${estado_bot['stake']}!", cor="text-green-500 font-bold")
    
    ordem = {
        "buy": 1, "price": estado_bot["stake"],
        "parameters": {
            "amount": estado_bot["stake"], "basis": "stake", "contract_type": direcao,
            "currency": "USD", "duration": 1, "duration_unit": "m", "symbol": estado_bot["ativo"]
        }
    }
    await ws_deriv.send(json.dumps(ordem))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
