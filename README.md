# SMC Trading Bot - Minimal Architecture

**Arquitetura estática e minimalista com apenas 4 arquivos, pronta para deploy em GitHub Pages em 1 clique.**

## 📁 Estrutura de Arquivos

```
smc-minimal/
├── package.json              # Dependências mínimas
├── vite.config.js            # Configuração Vite com base: './'
├── index.html                # HTML com Tailwind CDN + LightweightCharts
├── src/
│   ├── App.jsx               # Componente principal (SMC + WebSocket)
│   └── main.jsx              # Entry point React
├── .gitignore                # Arquivos ignorados
└── README.md                 # Este arquivo
```

## 🚀 Instalação Local

```bash
# 1. Instalar dependências
npm install

# 2. Rodar em desenvolvimento
npm run dev

# 3. Acessar em http://localhost:5173
```

## 📦 Build e Deploy

### GitHub Pages (Recomendado - 1 clique)

```bash
# 1. Criar repositório no GitHub
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/seu-usuario/smc-trading-bot.git
git push -u origin main

# 2. Ativar GitHub Pages (Settings → Pages → Deploy from a branch → main)

# 3. Deploy automático
npm run deploy

# Seu bot estará em: https://seu-usuario.github.io/smc-trading-bot
```

### Render

```bash
# 1. Fazer upload para GitHub (veja acima)

# 2. Conectar em render.com
# - New → Static Site
# - Conectar seu repositório GitHub
# - Build Command: npm run build
# - Publish Directory: dist

# Seu bot estará em: https://seu-smc-bot.onrender.com
```

### Netlify

```bash
# 1. Fazer upload para GitHub (veja acima)

# 2. Conectar em netlify.com
# - Add new site → Import an existing project
# - Selecionar GitHub e seu repositório
# - Build Command: npm run build
# - Publish Directory: dist

# Seu bot estará em: https://seu-smc-bot.netlify.app
```

## 🎯 Como Usar

### 1. Obter Token API Deriv
- Acesse [deriv.com](https://deriv.com)
- Faça login ou crie uma conta
- Vá para Settings → API tokens
- Crie um novo token com permissões "Read" e "Trade"

### 2. Conectar ao Bot
1. Cole o token no campo "Token API Deriv"
2. Clique em "CONECTAR"
3. Aguarde o gráfico carregar

### 3. Configurar Trade
1. Selecione o ativo (Volatility 10, EUR/USD, etc)
2. Configure o timeframe (5 Min ou 15 Min)
3. Defina o stake (valor a arriscar)
4. Defina Take Profit (ganho em $)
5. Defina Stop Loss (perda em $)

### 4. Analisar e Executar
1. Clique em "ANALISAR" para detectar sinais SMC
2. Quando um sinal aparecer, clique em "EXECUTAR TRADE"
3. O bot monitorará o P&L em tempo real
4. Quando atingir TP ou SL, fechará automaticamente

### 5. Parar Trading
- Clique em "⛔ PARAR" para pausar novos trades
- Clique em "▶ RETOMAR" para reativar

## 🧠 Lógica de Análise (Smart Money Concepts)

O bot detecta:
- **BOS (Break of Structure)**: Quando o preço quebra máximas/mínimas recentes
- **Order Blocks (OB)**: Blocos de liquidez
- **Suportes/Resistências**: Níveis calculados das últimas 50 velas

### Sinais Gerados
- **CALL (↗)**: Quando preço quebra resistência para cima
- **PUT (↘)**: Quando preço quebra suporte para baixo

## 💰 Gerenciamento de Risco

### P&L Real (Não é matemática simples!)
O bot solicita o P&L ao vivo da API Deriv:
```javascript
ws.send({
  proposal_open_contract: 1,
  contract_id: contractId,
  subscribe: 1
})
```

### Fechamento Automático
Quando P&L atinge:
- **Take Profit**: Fecha com ganho
- **Stop Loss**: Fecha com perda

## 🔐 Segurança

⚠️ **IMPORTANTE:**
- Nunca compartilhe seu token API
- Use apenas em contas DEMO para testar
- Não rode dois bots na mesma conta
- Valide sempre antes de usar em conta REAL

## 📊 Dependências

| Pacote | Versão | Uso |
|--------|--------|-----|
| react | 19.2.1 | Framework UI |
| react-dom | 19.2.1 | Renderização DOM |
| lucide-react | 0.453.0 | Ícones |
| vite | 7.1.7 | Build tool |
| @vitejs/plugin-react | 5.0.4 | Plugin React |
| gh-pages | 6.1.1 | Deploy GitHub Pages |

## 🌐 CDN Externas

- **Tailwind CSS**: `https://cdn.tailwindcss.com`
- **LightweightCharts**: `https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js`

## 🐛 Troubleshooting

### Gráfico não aparece
```
1. Verifique se o token é válido
2. Abra o console (F12) e procure por erros
3. Tente recarregar a página
4. Verifique se LightweightCharts carregou (console)
```

### Erro ao conectar
```
1. Verifique sua conexão com internet
2. Confirme que o token não expirou
3. Tente em conta demo primeiro
4. Verifique se a API Deriv está disponível
```

### Trades não executam
```
1. Verifique se trading não está pausado (⛔ PARAR)
2. Confirme que tem saldo suficiente
3. Verifique o console para erros
4. Tente analisar novamente
```

## 📈 Próximas Melhorias

- [ ] Histórico de trades com estatísticas
- [ ] Alertas por email/SMS
- [ ] Modo backtesting
- [ ] Múltiplos timeframes simultâneos
- [ ] Exportar dados em CSV

## 📞 Suporte

- **Documentação Deriv**: https://developers.deriv.com
- **Comunidade Deriv**: https://deriv.com

## 📄 Licença

MIT - Use livremente

---

**Versão**: 1.0.0  
**Arquitetura**: Minimalista (4 arquivos)  
**Deploy**: GitHub Pages, Render, Netlify  
**Status**: Pronto para produção
