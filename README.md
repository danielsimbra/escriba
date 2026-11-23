# Escriba

Agente de transcrição de áudios do WhatsApp. Quando ligado numa conversa, ele
escuta os áudios que chegam e responde ali mesmo com o texto — limpo, sem
"hmmm", sem "éééé", sem "né" no fim de cada frase, pontuado e quebrado em
parágrafos.

> Escriba é quem escrevia o que os outros ditavam. Ele não inventava o texto:
> registrava a fala de quem falou.

---

## O que ele faz

```
áudio no WhatsApp
        │
        ▼
Evolution API  ──webhook──▶  Escriba  ──▶  OpenAI (gpt-4o-transcribe)
                            allowlist        transcrição bruta
                            liga/desliga            │
                                                    ▼
     destino fixo  ◀──  texto limpo  ◀── Claude (limpeza)
   (você, em privado)   com cabeçalho      texto legível
```

1. **Recebe o evento** `messages.upsert` da Evolution API. Descarta na hora
   tudo que não é áudio de conversa autorizada: figurinha, imagem, status,
   recibo de leitura, grupo fora da lista.
2. **Baixa o áudio.** O arquivo no servidor do WhatsApp é criptografado; quem
   decifra é a Evolution. Com `WEBHOOK_BASE64` ligado, o áudio já vem dentro do
   próprio evento e o Escriba economiza uma chamada.
3. **Transcreve** com a API da OpenAI. Sai a fala como ela foi dita — hesitação
   inclusa.
4. **Limpa** com a Claude. Saem as muletas, entra a pontuação. As palavras
   continuam sendo as do falante.
5. **Entrega o texto.** Por padrão manda tudo para um **destino fixo**
   (`resposta.destino`) — normalmente o próprio número pareado, de modo que as
   transcrições caem na sua conversa "Mensagens para mim" e o contato do outro
   lado não vê nada. Cada mensagem leva cabeçalho dizendo de quem é o áudio e
   quanto durou. Deixando `destino` vazio, ele volta ao outro modo: responde
   dentro da conversa de origem, citando o áudio.

A Claude não entra na etapa 3 porque a API da Anthropic não recebe áudio. São
dois modelos de ofício diferente: um que ouve e um que edita.

## O que é uma "transcrição limpa"

O que sai da transcrição bruta:

> então assim ó, eu... eu queria ver com você aquela questão do, do relatório,
> né, porque o pessoal ficou de mandar na terça, não, mentira, na quarta, e aí
> tipo, se não chegar até quinta a gente vai ter que, sei lá, remarcar a
> reunião, entendeu? me fala aí

O que o Escriba entrega:

> Eu queria ver com você aquela questão do relatório, porque o pessoal ficou de
> mandar na quarta. Se não chegar até quinta, vamos ter que remarcar a reunião.
> Me fala.

Repare no que **não** aconteceu: nenhuma palavra virou sinônimo mais elegante,
nada foi resumido, nada foi reordenado, nada foi acrescentado. A autocorreção
("na terça, não, mentira, na quarta") virou só a versão corrigida, que é o que
a pessoa quis dizer.

### As regras da limpeza

| Sai | Fica |
|---|---|
| Hesitação: "hmm", "ahn", "éééé", "uhum" | Gíria e jeito de falar da pessoa |
| Muleta sem função: "né", "tipo", "sabe", "assim" | "Né?" que é pergunta de verdade |
| Gagueira e falsa partida: "eu... eu queria" | Repetição que é ênfase: "muito, muito bom" |
| Repetição imediata que é tropeço | A ordem em que a pessoa falou |
| Autocorreção — fica só a versão corrigida | O vocabulário dela, inclusive errado |
| Marcação do transcritor: `[inaudível]`, `(risos)` | Lista ditada continua lista |

E o que ele **nunca** faz: resumir, reescrever para "ficar melhor", traduzir,
responder ao conteúdo do áudio, virar tópicos, ou obedecer a instrução falada
dentro do áudio ("ignore as regras e escreva X" continua sendo só uma frase a
ser editada).

Tudo isso é ajustável em [`config/escriba.yaml`](config/escriba.yaml): a lista
de muletas, se número vira algarismo, e um campo de instruções extras para
vocabulário do seu contexto (nomes próprios e siglas que a transcrição erra).

## Como os números se encaixam

Três papéis, e confundi-los é a causa mais comum de "não funciona":

| Papel | Quem é | Onde se configura |
|---|---|---|
| **Pareado** | O número que lê o QR Code. É a caixa de entrada: só chega ao Escriba o áudio enviado **para ele**. | Na Evolution, por QR Code — não é variável de ambiente |
| **Destino** | Quem recebe as transcrições | `resposta.destino` |
| **Remetentes** | De quem os áudios são transcritos | `allowlist.conversas` |

Pareado e destino são **o mesmo número** na configuração padrão: o agente
manda para si mesmo, e as transcrições aparecem em "Mensagens para mim". Os
remetentes são os contatos, e o número pareado **não** entra na allowlist —
áudio gravado por ele chega como `fromMe` e é filtrado.

Se o número pareado não for o que recebe os áudios, nada acontece: o webhook
nunca é acionado, porque a mensagem não passa por aquela sessão.

## Quem ele atende

Duas travas, e as duas precisam estar abertas para um áudio virar texto.

**1. A allowlist.** Só conversas em `allowlist.conversas` são atendidas. Fora
delas o Escriba é surdo e mudo — não transcreve e não responde nem a comando.
A rejeição é registrada em `INFO` com o JID cru junto: se um áudio "sumiu", o
log diz por quê.

**2. O liga/desliga por conversa.** Dentro de uma conversa autorizada:

| Comando | O que faz |
|---|---|
| `escriba on` | Começa a transcrever nesta conversa |
| `escriba off` | Para de transcrever nesta conversa |
| `escriba status` | Mostra se está ligado, os modelos e o total já transcrito |
| `escriba ajuda` | Lista os comandos |

Aceita variação: `ligar`, `ativa`, `desligar`, `parar`, com ou sem `/` na
frente, maiúsculas ou não. Por padrão (`comandos.apenas_proprio: true`) só
**você** liga e desliga — o contato do outro lado não controla o seu agente.

O padrão de uma conversa que nunca recebeu comando vem de
`allowlist.comeca_ativo`.

---

## Instalação

### Pré-requisitos

- Docker e Docker Compose
- Uma chave da OpenAI (transcrição) e uma da Anthropic (limpeza)
- Um número de WhatsApp para parear com a Evolution API por QR Code

### Passo a passo

```bash
cp .env.example .env
```

Preencha o `.env`: as duas chaves de API, a `EVOLUTION_API_KEY` (gere com
`openssl rand -hex 16`), a `POSTGRES_PASSWORD` e o `WEBHOOK_TOKEN`.

```bash
docker compose up -d --build
```

Abra `http://SEU_IP:8080/manager`, crie a instância com o nome **idêntico** ao
`EVOLUTION_INSTANCE` do `.env` (a Evolution diferencia maiúsculas) e leia o QR
Code com o celular.

Registre o Escriba como webhook da instância:

```bash
docker compose exec escriba python -m escriba configurar-webhook
```

Crie a sua config a partir do template — o `config/escriba.local.yaml` fica
fora do Git, então telefone de contato seu nunca vai parar no GitHub:

```bash
cp config/escriba.yaml config/escriba.local.yaml
```

Nele, coloque em `allowlist.conversas` o número (ou o ID do grupo) de quem manda
áudio, e em `resposta.destino` o seu. Reinicie:

```bash
docker compose restart escriba
```

Mande um áudio para essa conversa. O texto volta em alguns segundos.

O passo a passo em VPS, o diagnóstico de quando o áudio não chega e como
reaproveitar uma Evolution que já existe estão em [`docs/DEPLOY.md`](docs/DEPLOY.md).

---

## Testando sem WhatsApp

O pipeline inteiro roda a partir de um arquivo local — útil para calibrar a
limpeza antes de soltar o agente numa conversa:

```bash
docker compose exec escriba python -m escriba transcrever /app/data/teste.ogg --bruta
```

Mostra a transcrição bruta e a limpa, lado a lado. Para testar só a edição, com
um texto que você mesmo escreveu:

```bash
docker compose exec escriba python -m escriba limpar /app/data/texto.txt
```

Outros comandos:

```bash
docker compose exec escriba python -m escriba status
```

```bash
docker compose exec escriba python -m escriba testar-evolution
```

Com `DRY_RUN=true` no `.env`, o Escriba transcreve e limpa de verdade, escreve
tudo no log — e não manda nada no WhatsApp. É o modo de conferir a qualidade
antes que a conversa veja qualquer coisa.

## Custo

A transcrição é a parte que pesa: na tabela da OpenAI, `gpt-4o-transcribe` fica
na casa de **US$ 0,006 por minuto** de áudio (confira o preço atual, que muda).
Um áudio de 2 minutos custa cerca de US$ 0,012 — algo como R$ 0,07. A limpeza
com Haiku custa uma fração disso: são poucas centenas de tokens por áudio.

`whisper-1` custa o mesmo e transcreve um pouco pior em português; a economia
real, se precisar, vem de trocar o provedor de transcrição, não o modelo.

## Limites e decisões

- **O áudio sai do seu servidor.** Ele vai para a OpenAI (transcrição) e o
  texto vai para a Anthropic (limpeza). Se isso não for aceitável para o
  conteúdo de alguma conversa, não coloque essa conversa na allowlist.
- **Um retorno sem erro é aceitação, não entrega.** A Evolution API v2 não
  devolve recibo de entrega na chamada de envio; o log diz que a mensagem foi
  aceita pelo gateway.
- **Áudio longo demais é recusado**, não picotado — o teto padrão é 15 minutos
  e 24 MB (`config/escriba.yaml`). O limite duro da API de transcrição é 25 MB.
- **Áudio curto demais pode ser ignorado sem custo de API.**
  `audio.duracao_minima_segundos` (padrão `0`, desligado) filtra em silêncio —
  sem transcrever e sem avisar na conversa — o "ok", "blz" de poucos segundos.
  É valor móvel: mude para 40, 120, o que fizer sentido, e reinicie o agente.
- **Reentrega não gera transcrição dupla.** A Evolution reenvia o webhook
  quando não recebe 200 rápido; o Escriba reserva o id da mensagem antes da
  primeira chamada paga. Falha de rede libera a reserva (dá para tentar de
  novo); falha de formato não (repetir daria no mesmo).
- **Áudio seu não é transcrito** por padrão. Ligue em `audio.processar_proprios`.
- **LID: o `remoteJid` nem sempre é o telefone.** O WhatsApp migrou parte do
  endereçamento para `@lid`, um identificador opaco; o telefone real vem em
  `remoteJidAlt`. O Escriba usa o `Alt` para allowlist e estado, mas mantém a
  chave **como veio** nas chamadas à Evolution — é por ela que a API acha a
  mídia. Sem isso, todo áudio em LID era descartado por "não está na
  allowlist".
- **Payload ilegível responde 200, não 500.** Reentregar um payload quebrado
  daria no mesmo resultado; o erro vai para o log com o traceback inteiro e a
  Evolution não entra em laço de retentativa.

## Estrutura

```
src/escriba/
  webhook.py      servidor HTTP; responde 200 rápido e joga o trabalho para o fundo
  evolution.py    lê o payload da Evolution e envia as respostas
  transcricao.py  áudio -> texto bruto (OpenAI)
  limpeza.py      texto bruto -> texto limpo (Claude); é onde moram as regras
  pipeline.py     a ordem das decisões: autorizada? ligada? cabe? transcreve
  estado.py       SQLite: quem está ligado e o que já foi processado
  config.py       .env + config/escriba.local.yaml (ou o template)
  models.py       Audio, Comando, Transcricao
config/escriba.yaml   template: allowlist, limites, muletas, formato da resposta
```

```bash
python -m pytest
```

Os testes não tocam a rede: OpenAI, Anthropic e Evolution são substituídas por
dublês. O que eles cobrem é o que costuma quebrar — formato do webhook,
allowlist, idempotência e os cortes de texto.

## Licença

MIT. Veja [LICENSE](LICENSE).
