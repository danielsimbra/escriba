# Deploy e diagnóstico

Este documento cobre três coisas: subir o Escriba numa VPS, ligá-lo a uma
Evolution API que já existe, e o que fazer quando o áudio chega e o texto não
volta.

---

## 1. Subindo do zero numa VPS

Uma máquina de 2 vCPU e 2 GB dá conta: o trabalho pesado acontece nas APIs, não
aqui. O que consome memória é a Evolution com o Postgres, não o Escriba.

```bash
git clone <seu-repo> escriba && cd escriba
cp .env.example .env
```

No `.env`, o mínimo que precisa mudar:

| Variável | O quê |
|---|---|
| `OPENAI_API_KEY` | chave da transcrição |
| `ANTHROPIC_API_KEY` | chave da limpeza |
| `EVOLUTION_API_KEY` | `openssl rand -hex 16` — igual nos dois lugares onde aparece |
| `POSTGRES_PASSWORD` | `openssl rand -hex 16` |
| `WEBHOOK_TOKEN` | `openssl rand -hex 24` |
| `EVOLUTION_SERVER_URL` | `http://SEU_IP:8080` |

```bash
docker compose up -d --build
docker compose logs -f escriba
```

O log de subida diz o que ele vai fazer: modelos, instância e quantas conversas
estão autorizadas. Se aparecer *"Nenhuma conversa autorizada"*, ele vai ignorar
tudo até você preencher a allowlist — o que é o comportamento certo, e não um
erro.

### Parear o WhatsApp

Abra `http://SEU_IP:8080/manager`, entre com a `EVOLUTION_API_KEY` e crie a
instância com o nome **exatamente igual** ao `EVOLUTION_INSTANCE` do `.env`. A
Evolution diferencia maiúsculas: `Escriba` e `escriba` são instâncias
diferentes, e o nome errado devolve 404 nas rotas.

Leia o QR Code com o celular que vai receber os áudios.

### Registrar o webhook

```bash
docker compose exec escriba python -m escriba configurar-webhook
```

Isso registra `http://escriba:8000/webhook?token=...` na instância, ouvindo só
`MESSAGES_UPSERT`. O endereço é o nome do serviço no compose: a Evolution fala
com o Escriba pela rede interna do Docker, e a porta 8000 não precisa (nem
deve) estar aberta na internet.

Confirme:

```bash
docker compose exec escriba python -m escriba testar-evolution
```

Deve dizer *"Instancia conectada"* e imprimir o webhook registrado.

### Autorizar a conversa

Edite `config/escriba.yaml`, coloque o número (ou o ID do grupo) em
`allowlist.conversas` e reinicie só o agente:

```bash
docker compose restart escriba
```

O arquivo é montado como volume: não precisa rebuild da imagem.

### Firewall

A porta **8000 não deve ser publicada** — no `docker-compose.yml` ela está
presa em `127.0.0.1`. A **8080** (manager da Evolution) idealmente também fica
na loopback, publicada por um Nginx com TLS e Basic Auth. Manager exposto na
internet com só a API key é sessão de WhatsApp esperando dono novo.

---

## 2. Reaproveitando um gateway que já existe

Se você já roda uma Evolution API na máquina (a do Hugin, por exemplo), **não
suba a segunda**: duas brigam pela porta 8080 e pelos mesmos volumes.

**a)** Comente os serviços `evolution-api`, `postgres` e `redis` no
`docker-compose.yml`, e o bloco `depends_on` do serviço `escriba`.

**b)** Descubra o nome da rede do outro projeto:

```bash
docker network ls
```

**c)** Ligue o Escriba nela, trocando o bloco `networks` do arquivo:

```yaml
    networks: [gateway-net]

networks:
  gateway-net:
    external: true
    name: hugin_hugin-net
```

**d)** No `.env`, aponte para o serviço existente. O nome do host é o nome do
container da Evolution na rede compartilhada:

```
EVOLUTION_BASE_URL=http://evolution-api:8080
EVOLUTION_API_KEY=<a mesma AUTHENTICATION_API_KEY do gateway>
EVOLUTION_INSTANCE=Escriba
```

**e)** Crie no manager uma **instância nova**, separada da que o outro agente
usa, e pareie com o número que vai receber os áudios. Instância é a sessão de
um número; dois agentes na mesma instância recebem os eventos um do outro.

Uma instância pode ter só um webhook. Se você registrar o Escriba numa
instância que já entregava eventos para outro sistema, o registro anterior é
substituído.

---

## 3. Quando o áudio chega e o texto não volta

Comece sempre pelo log — ele foi escrito para responder essa pergunta:

```bash
docker compose logs -f escriba
```

### O log não mostra nada quando chega áudio

O evento não está chegando. Em ordem de probabilidade:

1. **Webhook não registrado.** Rode `testar-evolution` e veja se a URL aparece.
2. **A Evolution não alcança o Escriba.** Teste de dentro do container dela:
   ```bash
   docker compose exec evolution-api node -e "fetch('http://escriba:8000/saude').then(r=>r.text()).then(console.log)"
   ```
   Sem resposta, os dois não estão na mesma rede Docker.
3. **Instância errada.** O log do Escriba avisa: *"Evento da instancia 'X'
   descartado"*. O nome no `.env` tem que bater com o do manager, maiúsculas
   incluídas.
4. **Token.** Um 401 no log da Evolution significa `WEBHOOK_TOKEN` diferente do
   que está na URL registrada. Rode `configurar-webhook` de novo depois de
   mudar o token.

### O log diz "nao esta na allowlist"

O número está fora de `config/escriba.yaml`. Repare que grupo precisa do JID
completo, com `@g.us` — o número do grupo sozinho não serve. Para descobrir o
JID, mande qualquer áudio no grupo e leia o `remote_jid` no log.

### O log diz "Escriba desligado"

A conversa recebeu um `escriba off` em algum momento — o estado sobrevive a
restart, porque mora no SQLite. Mande `escriba on` na conversa, ou olhe o que
está registrado:

```bash
docker compose exec escriba python -m escriba status
```

### "Evolution nao devolveu o audio da mensagem"

A Evolution não conseguiu recuperar a mídia. O caminho robusto é fazer o áudio
vir dentro do próprio evento:

```yaml
      WEBHOOK_BASE64: "true"
```

Se preferir a busca sob demanda, a Evolution precisa ter guardado a mensagem —
o que exige `DATABASE_SAVE_DATA_NEW_MESSAGE: "true"`. Com as duas opções
desligadas, a mídia não existe em lugar nenhum para ser buscada.

### A transcrição volta vazia ("Não consegui identificar fala")

Ouça o áudio. Microfone abafado, música de fundo alta e áudio de menos de um
segundo realmente não produzem texto. Para investigar, ligue `SALVAR_AUDIOS=true`
no `.env` e rode o arquivo salvo direto pela CLI:

```bash
docker compose exec escriba python -m escriba transcrever /app/data/audios/<arquivo>.ogg --bruta
```

O `--bruta` mostra o que a transcrição ouviu antes da limpeza. Se a bruta tem
conteúdo e a limpa não, o problema é o prompt de limpeza, não o áudio.

Lembre que `SALVAR_AUDIOS` grava conteúdo privado de conversa em disco. Ligue
para depurar, desligue depois.

### O texto vem "editado demais"

Se ele estiver trocando palavras ou resumindo, o ajuste é em
`config/escriba.yaml`:

- tire itens de `limpeza.muletas` que na sua fala carregam sentido;
- use `limpeza.instrucoes_extras` para fixar grafias que ele erra
  (`'Grafias corretas: "Databricks", "PySpark".'`);
- para o modelo ser mais literal ainda, um modelo maior em `ANTHROPIC_MODEL`
  segue instrução com mais disciplina que o Haiku em texto longo.

Teste a calibragem sem gastar áudio: escreva um arquivo com a transcrição bruta
e rode `python -m escriba limpar arquivo.txt`.

### A resposta chega cortada

O log avisa: *"bateu o teto de N tokens"*. Reduza
`limpeza.max_caracteres_por_lote` (que divide a transcrição em pedaços) ou
aumente `ANTHROPIC_MAX_TOKENS`.

---

## 4. Manutenção

**Backup.** Dois lugares importam: `data/escriba.db` (quem está ligado e o
histórico) e o volume `evolution_instances` (a sessão do WhatsApp — perdê-la
significa parear o QR Code de novo).

```bash
docker compose exec escriba python -c "import shutil; shutil.copy('/app/data/escriba.db','/app/data/escriba-backup.db')"
```

**Atualização.**

```bash
git pull && docker compose up -d --build escriba
```

Só o agente reinicia; a sessão do WhatsApp fica de pé.

**Poda do histórico.** O banco cresce devagar (uma linha por áudio), mas dá
para podar:

```bash
docker compose exec escriba python -c "import sys; sys.path.insert(0,'/app/src'); \
from escriba.config import load_settings; from escriba.estado import Estado; \
print(Estado(load_settings().db_path).limpar_antigas(30), 'linhas removidas')"
```

Cuidado: um id removido pode ser reprocessado se a Evolution reentregar aquele
evento. Na prática isso só acontece em minutos, não em dias — por isso 30 dias
é folgado.
