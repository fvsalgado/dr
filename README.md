# PA Monitor — Diário da República

Monitor automático de publicações do Diário da República para consultoria de public affairs.

## Clientes monitorizados
- **Ryanair** — aviação, aeroportos, companhias aéreas, ETS/ETD
- **Expedia Group** — turismo, OTAs, alojamento local, distribuição turística
- **Bimbo** — alimentação, rotulagem, IVA, panificação
- **Dow Portugal** — química, ambiente, energia, Estarreja, hidrogénio
- **Gasib** — gás natural, GPL, ERSE
- **Kaspersky** — cibersegurança, RGPD, CNCS

---

## Estrutura do projeto

```
├── .github/workflows/
│   ├── daily.yml              # Cron (dias úteis) + scrapers + commit + email
│   └── ci.yml                 # pytest em push/PR
├── data/
│   ├── results.json           # Resultados acumulados (auto-gerado)
│   └── backups/               # Snapshots por execução
├── email/
│   └── digest.py              # Digest diário (Mailgun)
├── keywords/
│   ├── clients.json           # Keywords por cliente
│   └── source_labels.json     # Nomes e logos por `source` (dashboard + digest)
├── scraper/
│   ├── dre.py                 # Diário da República
│   ├── parlamento.py          # Agenda e iniciativas
│   ├── news.py                # Imprensa (várias fontes news-*.py)
│   ├── news_common.py         # HTTP, RSS, meta HTML, rubrica Opinião
│   ├── source_meta.py         # Carrega source_labels.json
│   └── run_all.py             # Orquestra alvo a alvo (não paralelizar: mesmo JSON)
├── tests/                     # pytest (ver requirements.txt)
├── server.py                  # Dev: estático + POST /api/translate
└── index.html                 # Dashboard (GitHub Pages)
```

**Imprensa / Opinião:** artigos cujo título contém a palavra «opinião» são classificados como rubrica Opinião (`article_section` / `type`) e podem filtrar-se na vista Imprensa no dashboard.

**Testes:** `pip install -r requirements.txt` e `pytest tests/ -v`.

---

## Setup inicial (passo a passo)

### 1. Criar o repositório GitHub

```bash
git init
git remote add origin https://github.com/SEU_USER/pa-monitor.git
git add .
git commit -m "init: PA Monitor"
git push -u origin main
```

### 2. Ativar GitHub Pages

No repositório: **Settings → Pages → Source: Deploy from branch → main → / (root)**

O dashboard fica disponível em: `https://SEU_USER.github.io/pa-monitor/`

### 3. Configurar a password do dashboard

1. Gera o hash SHA-256 da tua password em: https://emn178.github.io/online-tools/sha256.html
2. Abre `index.html` e substitui o valor de `PASSWORD_HASH`
3. A password padrão de desenvolvimento é `pamonitor2025`

### 4. Configurar os secrets do GitHub Actions

Em **Settings → Secrets and variables → Actions → New repository secret**, adiciona:

| Secret | Valor |
|--------|-------|
| `MAILGUN_API_KEY` | Chave da API Mailgun (obtém em mailgun.com) |
| `MAILGUN_DOMAIN` | Domínio Mailgun (ex: `mg.teudominio.pt`) |
| `EMAIL_FROM` | Endereço de envio (ex: `monitor@mg.teudominio.pt`) |
| `EMAIL_TO` | Destinatários separados por vírgula |

> O plano gratuito do Mailgun permite 1.000 emails/mês — suficiente para uso diário.

### 5. Testar o scraper manualmente

```bash
python scraper/dre.py                         # Hoje
python scraper/parlamento.py --days 30       # Parlamento
python scraper/news.py --sources all          # Todas as fontes de imprensa
python scraper/news.py --sources news-eco,news-expresso
python scraper/run_all.py --targets all --days 30
python scraper/run_all.py --targets dre,news --news-sources news-eco
```

**Publituris:** o scraper usa o **sitemap** indicado em `robots.txt` (`/sitemap-index.xml` → `sitemap-0.xml` …), com User-Agent de browser. A rota `/rss` costuma devolver HTML (Next.js), não XML fiável.

### 5b. Abrir o dashboard localmente (evitar `file://`)

O `index.html` usa `fetch()` para ler `data/results.json`. Se abrires o ficheiro diretamente (URL `file://`), o browser bloqueia o pedido.

```bash
python server.py
```

Depois abre `http://localhost:8000`.

### 5c. Weekly Digest (tradução com IA)

O botão “Traduzir para inglês com IA” funciona via `server.py` para evitar CORS e não expor chaves no browser.

PowerShell:

```powershell
$env:ANTHROPIC_API_KEY="..."
python server.py
```

No GitHub Pages, para tradução sem expor chaves, configura um endpoint serverless e define no `index.html`:

```html
<script>
window.PA_TRANSLATE_ENDPOINT = "https://SEU-WORKER.workers.dev";
</script>
```

Existe um exemplo de worker em `cloudflare/worker.js`.

### 6. Forçar execução do workflow

Em **Actions → Daily PA Monitor → Run workflow**.

Podes escolher:
- `targets`: `all` ou subset (`dre,parlamento,news`)
- `news_sources`: `all` ou subset (`news-eco,news-expresso,news-observador,news-jornaldenegocios,...`)
- `days`: janela para DRE/Parlamento
- `strict_mode`: `true` para falhar o workflow se algum scraper falhar
- `backup_retention_days`: número de dias para manter snapshots antigos

O workflow cria sempre:
- `data/results.backup.json` (backup de segurança)
- snapshots em `data/backups/` com timestamp
- artifact `results-backups` para recuperação

---

## Personalização de keywords

Edita `keywords/clients.json` para ajustar os termos por cliente. O motor usa regex case-insensitive com fronteiras de palavra, portanto `"gás"` não apanha `"gasoso"`.

---

## Adicionar scrapers futuros

1. Cria `scraper/parlamento.py` seguindo a mesma interface que `dre.py`
2. Adiciona o passo no `.github/workflows/daily.yml`
3. O `results.json` aceita qualquer valor de `"source"` — o dashboard mostra-o automaticamente

---

## Manutenção

- **Resultados acumulam** em `data/results.json` com deduplicação por ID
- Para limpar o histórico: apaga `data/results.json` e faz push (o ficheiro vazio é recriado)
- Os logs de cada execução estão em **Actions → Daily PA Monitor → [run]**

---

## Secrets a configurar no GitHub

Vai a **Settings → Secrets and variables → Actions** e adiciona:

| Secret | Valor |
|--------|-------|
| `MAILGUN_API_KEY` | A tua API key do Mailgun |
| `MAILGUN_DOMAIN` | Domínio Mailgun (ex: `sandbox....mailgun.org` ou domínio próprio) |
| `EMAIL_FROM` | Ex: `monitor@teudominio.pt` ou `mailgun@sandbox....mailgun.org` |

> Os destinatários (`fsalgado@atrevia.com`, `bchambel@atrevia.com`) já estão no workflow.
> ⚠️ Revoga a API key exposta e cria uma nova antes de usar em produção.

---

## Nota sobre o scraper do DRE (2026)

O portal passou a exigir `versionInfo` e os endpoints antigos fazem redirect. Se o scraper indicar `hasApiVersionChanged=true` e não devolver itens, captura o `versionInfo` real nos pedidos de rede do browser e exporta:

PowerShell:

```powershell
$env:DRE_VERSION_INFO_JSON='{"...":"..."}'
python scraper/dre.py --days 1
```
