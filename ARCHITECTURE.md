# ARCHITECTURE.md

Documento de decisões técnicas do backend do assistente de IA da Realmate.
O objetivo aqui não é descrever o que o código faz — isso o código faz melhor —
mas **por que ele está organizado desse jeito**, quais alternativas foram
consideradas e onde estão os limites conscientes da solução.

---

## 1. Visão geral

```
                POST /webhook/message
                         │
                         ▼
              ┌────────────────────┐
              │  webhooks (view)   │  valida payload, responde rápido
              └─────────┬──────────┘
                        │ register_customer_message()
                        ▼
              ┌────────────────────┐
              │   conversations    │  Conversation / Message /
              │  (models+services) │  PropertyRecommendation
              └─────────┬──────────┘
                        │ schedule_conversation_processing(countdown=10s)
                        ▼
              ┌────────────────────┐
              │  Celery worker     │  process_conversation
              │ conversations.tasks│  (debounce + orquestração)
              └─────────┬──────────┘
                        │ agent.run_sync(history, deps)
                        ▼
              ┌────────────────────┐        ┌──────────────────┐
              │     assistant      │◄──────►│    properties    │
              │ agent/tools/schemas│ tools  │ models+importers │
              └────────────────────┘        └──────────────────┘
                        │                            ▲
                        │ persiste resposta          │ Celery Beat (diário)
                        ▼                            │
              GET /api/conversations/{phone}/messages │
```

Quatro apps Django, cada um com um recorte de domínio próprio, mais um app
`common` com o que é genuinamente transversal.

| App             | Responsabilidade                                                        |
| --------------- | ----------------------------------------------------------------------- |
| `webhooks`      | Fronteira HTTP com o provedor de mensageria. Só transporte e validação.  |
| `conversations` | Domínio de conversa: mensagens, histórico, recomendações, orquestração.  |
| `properties`    | Domínio de imóvel: modelo de busca e carga de dados (ETL).               |
| `assistant`     | Integração com IA: agente, tools, contratos e conversão de histórico.    |
| `common`        | Modelo base com timestamps e validador de telefone.                      |

### Por que essa separação, e não um app só

A tentação num desafio deste tamanho é criar um app `core` com tudo dentro.
Optei por separar porque os quatro recortes têm **ciclos de vida e razões de
mudança diferentes**:

- `webhooks` muda quando o **provedor de mensageria** muda (novo evento, novo
  formato de payload). Nada disso deveria tocar o modelo de conversa.
- `properties` muda quando **entra uma nova fonte de imóveis** (XML, API REST).
  Nada disso deveria tocar a IA.
- `assistant` muda quando **muda o modelo, o prompt ou o contrato das tools**.
  Nada disso deveria tocar o webhook.
- `conversations` é o núcleo estável: telefone, mensagem, histórico,
  recomendação. É o que menos deve mudar.

Isso é separação por **eixo de mudança**, não por camada técnica. Não há um app
`services/` global nem um `repositories/` genérico: cada app carrega o seu
`services.py`, `tasks.py` e `models.py`. Repositórios explícitos foram
descartados — o ORM do Django já é a camada de acesso a dados, e envolvê-lo em
outra abstração aqui seria cerimônia sem retorno.

### Por que `webhooks` é um app separado de `conversations`

Foi a decisão mais discutível. O webhook cria mensagens de conversa, então
poderia viver dentro de `conversations`. Mantive separado porque o webhook é um
**contrato com um sistema externo**, não uma regra do domínio: ele lida com
envelope de evento, idempotência de entrega, tipos de evento que ignoramos e o
tempo de resposta exigido pelo provedor. Quando aparecer `MESSAGE_READ`,
`MESSAGE_DELIVERED` ou um segundo provedor, o crescimento fica contido num app
cuja única razão de existir é essa fronteira — e `conversations` continua
falando só de conversa.

---

## 2. Modelagem de dados

### `Property` (`properties/models.py`)

Uma única tabela para todos os imóveis, independentemente da origem — CSV e
JSON são mesclados, como o desafio pede.

Decisões:

- **`code` é `unique=True` em coluna própria.** É a chave de negócio: é o que o
  cliente cita no WhatsApp, o que o `update_or_create` usa como chave de
  unicidade da carga e o que aparece em `properties_found` na API. A PK
  continua sendo o `BigAutoField` do Django — chave de negócio como PK amarraria
  o banco a um identificador que um parceiro externo controla.
- **`price` é `DecimalField`, não `Float`.** Dinheiro nunca em ponto flutuante.
  Os filtros `>=` / `<=` da tool comparam `Decimal` de ponta a ponta.
- **`transaction_type` e `source` são `TextChoices`** (`properties/enums.py`).
  Os valores de `TransactionType` são `"aluguel"` / `"venda"` — os mesmos termos
  que o cliente usa e que a tool aceita. Isso elimina uma camada de tradução
  entre o vocabulário do cliente, o contrato da tool e o filtro do ORM.
- **Rastro de origem:** `source`, `source_reference` e `imported_at`. O desafio
  chamava isso de opcional ("se considerar relevante"); considerei relevante
  porque quando o mesmo `code` vier de duas fontes com preços diferentes, a
  primeira pergunta será "de onde veio esse registro e quando". Sem essas três
  colunas, não há resposta.
- **Índice composto `(transaction_type, neighborhood, price)`.** É exatamente a
  forma da consulta obrigatória da tool de busca — o caso não-código sempre
  filtra por esses três campos, nessa ordem de seletividade.

### `Conversation`, `Message`, `PropertyRecommendation` (`conversations/models.py`)

- **`Conversation.user_phone` é `unique`.** A premissa do desafio é "um telefone
  = uma conversa", então a unicidade é do banco, não de uma checagem em Python.
  É isso que faz `get_or_create` ser seguro sob concorrência: duas mensagens
  simultâneas do mesmo cliente não criam duas conversas.
- **`Message.external_id` é `UUIDField(unique=True)`.** A idempotência do
  webhook é **uma constraint de banco**, não um `if` na view. Reentrega do
  provedor cai no `get_or_create` e volta `created=False`. Nenhuma janela de
  race condition entre o "já existe?" e o `INSERT`.
- **`timestamp` separado de `created_at`.** `timestamp` é o momento informado
  pela origem (o que ordena o histórico e o que a API devolve); `created_at`
  (herdado de `TimestampedModel`) é o momento do `INSERT`. Confundir os dois faz
  o histórico ficar fora de ordem no primeiro reprocessamento ou atraso de fila.
- **`PropertyRecommendation` é uma tabela `through` explícita**, não um M2M
  simples. Motivos: (1) `UniqueConstraint(conversation, property)` transforma
  "nunca recomendar o mesmo imóvel duas vezes na mesma conversa" numa **garantia
  do banco**, não numa promessa do prompt; (2) o `created_at` da tabela dá a
  ordem cronológica de `properties_found`; (3) o FK opcional para `Message` liga
  a recomendação à mensagem do assistente que a apresentou — rastreabilidade que
  um M2M puro não teria.
- **`on_delete=PROTECT` do lado do imóvel.** Uma recomendação já feita ao
  cliente é registro histórico. Apagar um imóvel não pode apagar a evidência de
  que ele foi oferecido.
- **Índice `(conversation, timestamp)`** — é a consulta do histórico e a do
  contexto do agente.
- **`last_message_at` é desnormalizado** de propósito. É pedido pelo desafio, e
  a alternativa (agregar `MAX(timestamp)` das mensagens a cada leitura) custa um
  join numa informação lida com frequência. `touch_last_message_at` só avança o
  valor, nunca retrocede — mensagem que chega atrasada não "rebobina" a conversa.

---

## 3. Carga de dados (ETL)

`properties/importers/` — o ponto do desafio que mais pedia desenho para o
futuro, já que XML e API REST vêm "nos próximos meses".

### Template Method sobre uma base abstrata

`PropertyImporter` (`etl/base.py`) define três partes:

```python
class PropertyImporter(ABC):
    source: str

    @abstractmethod
    def extract(self) -> Iterator[Any]: ...      # varia por formato

    @abstractmethod
    def transform(self, raw: Any) -> PropertyData: ...  # varia por formato

    @final
    def load(self) -> ImportResult: ...          # NÃO varia — nunca
```

`extract` e `transform` são o que muda entre formatos. `load` — upsert por
`code`, contagem de criados/atualizados/ignorados, coleta de erros — é idêntico
para qualquer fonte e por isso é `@final`: um importador novo **não pode**
reimplementar a regra de idempotência da carga e divergir dos outros.

Adicionar XML é, literalmente, escrever duas funções. `etl/xml.py` e
`etl/api_rest.py` já estão no repositório como esqueletos com as assinaturas
corretas e `NotImplementedError` — não como código morto, mas como prova
executável de que a extensão cabe sem tocar em nada existente: nem em `base.py`,
nem nos importadores atuais, nem no modelo.

**Alternativas descartadas:**
- *Um `management command` por formato* — duplicaria a lógica de upsert em cada
  comando, exatamente o que o desafio pede para evitar.
- *Registry com plugins auto-descobertos* — resolveria um problema que não
  temos. Com duas fontes ativas, a lista explícita em `properties/tasks.py` é
  mais legível e mais fácil de depurar do que descoberta dinâmica.

### Idempotência

`Property.objects.update_or_create(code=..., defaults={...})`. Carga repetida
atualiza; nunca duplica. A garantia final é a `unique` no banco, não a
aplicação. Escolhi **atualizar** em vez de ignorar porque preço e disponibilidade
mudam — um registro desatualizado faria o assistente informar um preço errado ao
cliente, que é o pior tipo de erro neste domínio.

### Fronteira tipada: `PropertyData`

`extract` devolve `Any` — é dado externo não confiável, e fingir que tem tipo
seria mentira. `transform` devolve `PropertyData`, um modelo Pydantic. Esse é o
ponto exato onde o dado deixa de ser "coisa de arquivo" e vira domínio. Depois
dele, nada abaixo lida com `dict[str, str]`.

### Parsers compartilhados

`etl/parsers.py` concentra `parse_price`, `parse_bedrooms`,
`parse_transaction_type` e `split_code_from_description`. CSV e JSON usam os
mesmos — a única diferença real entre os dois é o regex do código
(`codigo: X` no CSV, `ref: X` no JSON), que fica como atributo de classe. Cada
parser levanta `ValueError` com o valor original na mensagem; `load` captura,
registra em `ImportResult.errors` e **segue para o próximo registro**. Um imóvel
malformado não derruba a carga inteira.

### Agendamento

`properties.load_properties` é uma `@shared_task` agendada no
`CELERY_BEAT_SCHEDULE`. A task é registrada pelo `name=` explícito
(`"properties.load_properties"`), não pelo caminho do módulo — mover o arquivo
não quebra o agendamento em silêncio.

---

## 4. Webhook

`webhooks/views.py` faz quatro coisas, nessa ordem, e nada além: valida,
persiste, enfileira, responde.

### Validação em dois estágios

O payload chega com formatos diferentes por tipo de evento, e o envelope é a
única parte comum. Por isso:

1. `WebhookEnvelopeSerializer` valida só `{event, content}`.
2. Se `event != MESSAGE_RECEIVED`, responde `200 {"status": "ignored"}` e para —
   sem exigir nada do `content`. Isso importa: um `MESSAGE_READ` não tem
   `message_content`, e validá-lo com o schema de mensagem daria `400` num
   evento que o desafio manda aceitar e descartar.
3. Só então `MessageReceivedSerializer` valida o conteúdo específico.

`WebhookEvent` é um `StrEnum` com um único membro hoje. Eventos não listados
retornam `200` de propósito: um `4xx` faria o provedor reenviar indefinidamente
um evento que nunca vamos tratar.

### Idempotência

Delegada à constraint de `Message.external_id`. `register_message`
devolve um `MessageIngestion(message, conversation, created)`; quando
`created=False`, a view responde `"ignored"` e **não enfileira nada**. Sem essa
guarda, uma reentrega dispararia um segundo processamento de IA para uma
mensagem já respondida.

### Por que o webhook não chama a IA

Requisito do desafio, mas também a decisão certa: uma chamada de LLM leva
segundos e pode falhar. O provedor de mensageria tem timeout e política de
retry. Fazer a IA na thread do request converteria latência do modelo em
reentrega de webhook, que viraria processamento duplicado. O webhook faz só o
trabalho síncrono barato (validar + `INSERT`) e devolve o controle.

---

## 5. Debounce

Requisito: mensagens em até 10s na mesma conversa geram **um** processamento.

### Solução: countdown + verificação de supersessão

```python
# ao receber cada mensagem
process_conversation.apply_async(
    kwargs={"conversation_id": ..., "trigger_message_id": ...},
    countdown=settings.DEBOUNCE_WINDOW_SECONDS,
)

# ao executar, 10s depois
if has_newer_customer_message(conversation_id=..., message_id=trigger_id):
    return  # outra mensagem chegou depois; a task dela é que vai responder
```

Três mensagens rápidas agendam três tasks. As duas primeiras acordam, veem que
existe mensagem de cliente mais recente na conversa e retornam sem fazer nada.
A terceira processa, com as três mensagens no histórico. Uma resposta da IA.

**O que isso custa:** N tasks agendadas para N mensagens, sendo N-1 no-ops. São
tasks baratíssimas (uma query `EXISTS` com índice) e o volume de rajada é o de
um humano digitando. Trocar isso por lock distribuído significaria pagar
complexidade e um novo modo de falha para economizar duas queries.

**Alternativas descartadas:**
- *Chave de debounce no Redis com revoke da task anterior* — `revoke` no Celery
  não é garantido para tasks já entregues ao worker, então a verificação de
  supersessão continuaria necessária. Complexidade a mais pelo mesmo resultado.
- *Lock distribuído por conversa* — resolve um problema que a premissa do
  desafio explicitamente remove ("o cliente não envia outra mensagem até receber
  a resposta"). Seria a resposta certa para um sistema de produção sem essa
  premissa; aqui seria overengineering.

**Onde a decisão é frágil, e eu sei:** a verificação e o processamento não são
atômicos. Uma mensagem que chega no exato intervalo entre o `EXISTS` e o fim do
`run_sync` seria processada duas vezes. A premissa do desafio cobre esse caso;
num sistema real, o passo seguinte seria um lock por `conversation_id` no Redis
com TTL — e o desenho atual acomoda isso sem mudar nada além da task.

---

## 6. Camada de IA

### `pydantic-ai` em vez do SDK bruto da OpenAI

**Esta é uma divergência consciente do enunciado, que pede o SDK oficial da
OpenAI.** O que o desafio veda explicitamente é LangChain/LangGraph — frameworks
de orquestração que introduzem grafos, chains, memória própria e um modelo
mental paralelo ao da aplicação. `pydantic-ai` não é isso: é uma camada fina
sobre o SDK (o `openai` continua sendo a dependência que fala com a API) que
resolve três coisas concretas com tipagem de verdade:

1. **Contrato de tool derivado da assinatura da função.** A JSON Schema da tool
   sai da anotação de tipos de `search_properties` — não há um dict de schema
   escrito à mão que possa divergir da função que ele descreve.
2. **Saída estruturada validada.** `AgentReply` é um modelo Pydantic; a resposta
   do modelo é validada contra ele antes de virar registro no banco.
3. **`RunContext[AssistantDeps]` tipado**, que é como a tool recebe o
   `conversation_id` sem que ele passe pelo prompt (ver abaixo).

O trade-off: uma dependência a mais e menos controle sobre o loop de tool
calling. O ganho: `mypy --strict` cobre o caminho crítico onde o dado externo
menos confiável do sistema (a saída de um LLM) entra no domínio. Achei a troca
favorável — mas é a decisão deste documento com maior chance de ser revertida se
a avaliação priorizar a literalidade do enunciado. Migrar para o SDK puro é
contido: `assistant/agent.py` e a assinatura das tools; `tools.py`, `schemas.py`,
`history.py` e todo o resto do sistema ficam intactos.

### O agente é construído por request, não é global

`get_agent()` monta o `Agent` a cada processamento. Isso mantém `settings`
mutável nos testes (trocar modelo, apontar para `TestModel`) e evita estado
compartilhado entre execuções no worker. O custo é construir um objeto leve por
mensagem — irrelevante perto de uma chamada de rede a um LLM.

`build_model()` escolhe entre Ollama (local, `DEBUG=True`) e OpenAI
(`DEBUG=False`) pelo mesmo contrato de `Model`. Desenvolver e testar o loop de
tools sem gastar tokens e sem depender de rede foi um ganho real; o código de
domínio não sabe qual dos dois está atrás.

### Histórico: tradução explícita, não persistência do formato do SDK

`assistant/history.py` converte `Message` do banco em `ModelMessage` do
`pydantic-ai`. Poderia ter guardado o formato nativo do SDK direto na tabela e
economizado esse arquivo. Não fiz: o banco guarda o **domínio** (papel, conteúdo,
timestamp), não o formato de serialização de uma biblioteca. Trocar de SDK ou de
versão não deve exigir migração de dados nem invalidar o histórico das conversas
já existentes.

O histórico é limitado a `AGENT_HISTORY_MESSAGE_LIMIT` (30) mensagens mais
recentes — teto de custo e de contexto. Conversas de WhatsApp são longas e as
primeiras mensagens raramente importam para a resposta atual. A busca é
`ORDER BY timestamp DESC LIMIT 30` seguida de `reversed()` em Python, para pegar
as *últimas* 30 na ordem cronológica sem varrer a conversa inteira.

### Tratamento de falha

`process_conversation` captura `UsageLimitExceeded` e `UnexpectedModelBehavior`
e responde com `FALLBACK_MESSAGE`. A escolha é deliberada: **o cliente recebe
sempre uma resposta**. Um modelo em loop de tool calling ou devolvendo lixo é um
problema nosso, e o pior resultado seria o cliente ficar sem resposta nenhuma —
ele não sabe que precisa reenviar. O fallback pede os filtros obrigatórios de
volta, então também é útil, não só uma desculpa.

`UsageLimits(request_limit=AGENT_REQUEST_LIMIT)` é o teto duro contra loop
infinito de tool calling — proteção de custo que não depende do prompt.

---

## 7. Tools

### 7.1 Busca de imóveis — filtros obrigatórios são determinísticos

O requisito é categórico: *"Em hipótese alguma a IA deve conseguir enviar
imóveis se o cliente não preencheu os filtros obrigatórios."* Um prompt não
oferece essa garantia — prompt influencia, não garante.

Por isso a regra está **em código, dentro da tool**, e o prompt é apenas a
primeira linha de defesa (que evita a ida-e-volta no caso comum):

```python
if code:
    ...  # código dispensa os demais filtros

missing = []
if transaction_type is None: missing.append("tipo de transação (aluguel ou venda)")
if neighborhood is None:     missing.append("bairro")
if min_price is None and max_price is None: missing.append("preço mínimo, máximo ou faixa")

if missing:
    raise ModelRetry("Não é possível buscar sem estes filtros: ...")
```

Se o modelo chamar a tool sem os filtros obrigatórios, ele **não recebe imóveis**
— recebe um `ModelRetry` dizendo o que falta e que deve perguntar ao cliente. A
regra não é negociável por reformulação de prompt, nem por jailbreak, nem por
alucinação. O caminho pelo `code` sai antes de qualquer validação, como o
desafio permite.

**Por que `ModelRetry` e não uma exceção normal:** uma exceção mataria a
execução e cairia no fallback, que é uma resposta ruim para uma situação
perfeitamente recuperável. `ModelRetry` devolve o erro ao modelo como
observação, e ele reformula — pergunta o bairro em vez de buscar. O erro vira
instrução.

### Guidance no retorno da tool

`PropertySearchResult` carrega `properties` **e** um campo `guidance` com texto
imperativo: o que fazer com esse resultado.

```python
FOUND = "Apresente estes imóveis ao cliente: código, bairro, preço, quartos..."
NOTHING_FOUND = "Nenhum imóvel encontrado... NÃO repita a busca com filtros que o cliente não informou."
SEARCH_BUDGET_SPENT = "Limite de buscas para esta mensagem atingido..."
```

O motivo é empírico: com a tool devolvendo lista vazia e nada mais, o modelo
tende a "ajudar" alargando os filtros por conta própria — busca de novo em outro
bairro, sobe o teto de preço, remove o filtro de quartos. Isso viola a regra de
não inventar filtros que o cliente não deu. Instrução no system prompt ajuda,
mas ela está longe, no início do contexto; a `guidance` chega junto com o
resultado, no momento exato da decisão. É prompt engineering posicionado onde
tem efeito.

### `MAX_SEARCHES_PER_RUN = 3`

Teto de buscas por mensagem processada, guardado em `AssistantDeps.searches_done`.
Uma mensagem legítima raramente precisa de mais de duas buscas ("tem 2 quartos
em Boa Viagem? e em Casa Forte?"). Além disso é sintoma de loop. Ao estourar, a
tool devolve `SEARCH_BUDGET_SPENT` em vez de resultados — degrada em vez de
falhar, e o cliente recebe o que já foi encontrado.

### Exclusão de imóveis já recomendados

A regra "nunca recomendar de novo na mesma conversa" também **não depende do
modelo**. Toda busca — inclusive a por código — aplica:

```python
already_recommended = PropertyRecommendation.objects.filter(
    conversation_id=ctx.deps.conversation_id
).values_list("property_id", flat=True)
...
Property.objects.filter(**params).exclude(id__in=already_recommended)
```

O modelo não tem como recomendar um imóvel repetido, porque ele nunca chega a
vê-lo. Não há parâmetro de tool para "excluir os já vistos" que o modelo pudesse
esquecer de passar — a exclusão é incondicional.

**`conversation_id` vem de `AssistantDeps`, nunca do modelo.** É a razão de a
tool receber `RunContext[AssistantDeps]`: se o `conversation_id` fosse um
parâmetro da tool, o modelo poderia alucinar um número e ler as recomendações de
outro cliente. Identidade de conversa é dado de sessão, não argumento de função
exposto ao LLM.

### `MAX_RESULTS = 2` no código

O limite de 2 imóveis por busca é fatiamento de queryset (`found[:MAX_RESULTS]`),
não pedido no prompt. Mesma lógica de sempre: regra de negócio verificável vai
para o código.

### Dupla contabilidade da recomendação

Em `process_conversation`:

```python
recommended_codes = [p.code for p in reply.recommended_properties] or deps.presented_codes
```

A fonte primária é o que o modelo declarou ter recomendado no `AgentReply`. O
fallback é `deps.presented_codes` — os códigos que a tool efetivamente entregou
durante a execução, acumulados pela própria tool. Se o modelo apresenta imóveis
no texto mas esquece de preencher o campo estruturado, a recomendação ainda é
registrada e aqueles imóveis não voltam a aparecer. Persistir "o que o cliente
viu" é mais importante do que persistir "o que o modelo disse que mostrou".

### 7.2 FAQ — o arquivo inteiro, sem busca

A tool devolve as 10 perguntas e respostas do JSON. Sem embeddings, sem vetor,
sem RAG.

**Justificativa:** são 10 entradas, ~2 KB de texto. Cabem folgadamente no
contexto de qualquer modelo atual. RAG existe para quando o corpus não cabe no
contexto — introduzir banco vetorial, pipeline de embedding e tuning de
similaridade para 10 registros seria a bazuca que o próprio enunciado pede para
evitar. E seria *pior*: busca por similaridade pode não trazer a entrada certa,
enquanto o contexto completo garante que o modelo tem a informação se ela
existir.

`_load_faq()` é `@lru_cache(maxsize=1)`: o arquivo é lido do disco uma vez por
processo de worker. As entradas são validadas em `FaqEntry` na leitura, então um
JSON malformado falha no carregamento, não no meio de um atendimento.

**Quando isso deixa de valer:** algumas centenas de entradas. Aí a troca é
substituir o corpo de `faq_properties()` — a assinatura da tool e o prompt não
mudam. A decisão é reversível, o que é o que a torna segura.

O prompt instrui explicitamente a responder "não tenho essa informação" quando a
resposta não estiver na base. Aqui a garantia é mesmo do prompt: não há como
validar deterministicamente que uma resposta em linguagem natural derivou do
corpus. O mitigante estrutural é que o modelo recebe *apenas* as 10 entradas
como fonte, sem nada mais que ele pudesse citar como se fosse política da
imobiliária.

---

## 8. API de saída

`GET /api/conversations/{user_phone}/messages` — `RetrieveAPIView`, sem regra de
negócio.

- **Ordenação explícita no `Prefetch`**, não em `Meta.ordering`. O formato é
  contrato verificado por testes automatizados; deixar a ordem no query site
  garante que mudar o default de ordenação de um modelo não quebre a resposta em
  silêncio.
- **`prefetch_related` de mensagens e recomendações** — histórico inteiro em 3
  queries, independente do tamanho da conversa. `select_related("property")` na
  recomendação evita N+1 ao montar `properties_found`.
- **`normalize_phone`** aceita a URL com ou sem `+`. O `+` é literal num path,
  mas alguns clientes o omitem ao montar a URL; como o telefone é a chave da
  conversa, normalizar evita um 404 enganoso por detalhe de encoding.
- **Formato de timestamp fixado** em `%Y-%m-%dT%H:%M:%SZ` com
  `default_timezone=UTC`, porque o formato exato faz parte do contrato.
- **Validação de telefone no `re_path`** (`\+?\d{12,13}`): formato inválido nem
  chega à view.

---

## 9. Tipagem

`mypy` com `disallow_untyped_defs`, `disallow_incomplete_defs`,
`check_untyped_defs`, `warn_return_any` e `possibly-undefined`.

Decisões que existem por causa da tipagem:

- **`MessageIngestion` como dataclass frozen** em vez de tupla. O retorno de
  `register_message` é `(message, conversation, created)` — como tupla,
  cada chamador precisaria lembrar da ordem.
- **Modelos Pydantic nas fronteiras.** `PropertyData` (ETL), `AgentReply` e
  `PropertySearchResult` (IA), `AssistantDeps` (contexto de tool). Todo ponto
  onde dado externo entra no sistema tem um tipo declarado.
- **`validated_data` do DRF não atravessa a view.** É `dict[str, Any]`, tipo que
  não ajuda ninguém abaixo da view. O que desce para o serviço são argumentos
  nomeados com `UUID` e `datetime` de verdade.
- **`Manager` declarado nos modelos** (`messages: models.Manager["Message"]`)
  para que o mypy enxergue os acessores reversos.
- **`TYPE_CHECKING`** em `properties/models.py` para tipar a relação com
  `conversations` sem criar import circular em runtime.

Não há `# type: ignore` no código de produção. A única ocorrência está em
`assistant/tests/test_tools.py`, num helper que repassa `**filters` para a
tool — o `mypy` não consegue verificar o `unpack` de um dict genérico contra a
assinatura, e escrever cada teste com argumentos nomeados custaria legibilidade
sem ganho de segurança.

---

## 10. Testes

~1.400 linhas em `src/*/tests/`, concentradas onde o erro custa caro:

- **`assistant/tests/test_tools.py`** — as regras determinísticas: filtro
  obrigatório ausente levanta `ModelRetry`; busca por código ignora os demais
  filtros; imóvel já recomendado nunca retorna; teto de 2 resultados; orçamento
  de buscas esgotado.
- **`assistant/tests/test_agent.py`** — construção do agente, seleção de modelo,
  `reply_from_text` com JSON válido e com texto puro.
- **`conversations/tests/test_tasks.py`** — supersessão do debounce, fallback nos
  dois tipos de exceção, persistência da resposta, registro de recomendação pelos
  dois caminhos (declarado e `presented_codes`).
- **`conversations/tests/test_views.py`** — o formato exato da API.
- **`conversations/tests/test_services.py` / `test_models.py`** — idempotência,
  `touch_last_message_at` que não retrocede, constraints.

O que **não** é testado: a qualidade da resposta do LLM. Não é determinístico e
não é testável em unidade. O que é testado é tudo que o cerca — as guardas que
funcionam independentemente do que o modelo faça.

---

## 11. Trade-offs assumidos

| Decisão                                   | Ganho                                       | Custo aceito                                         |
| ----------------------------------------- | ------------------------------------------- | ---------------------------------------------------- |
| `pydantic-ai` em vez do SDK puro           | Tipagem no caminho crítico, schema derivado | Divergência do enunciado; menos controle do loop     |
| Debounce por countdown + supersessão       | Sem lock, sem estado extra                  | N-1 tasks no-op; não é atômico                       |
| Uma tabela para todos os imóveis           | Busca simples e rápida                      | Campos específicos de fonte não têm onde morar       |
| FAQ inteiro no contexto                    | Zero infra, sem falso negativo de busca     | Não escala além de ~centenas de entradas             |
| Fallback em vez de propagar erro           | Cliente sempre recebe resposta              | Falha do modelo fica menos visível (só em log)       |
| `last_message_at` desnormalizado           | Leitura barata                              | Precisa ser mantido a cada escrita                   |
| Sem autenticação                           | Pedido do desafio                           | Não é deployável em produção como está               |

---

## 12. Limitações conhecidas e próximos passos

Itens que identifiquei e conscientemente não resolvi, com o encaminhamento:

1. **`CELERY_BEAT_SCHEDULE` está com `crontab(minute="*")`** — de minuto em
   minuto, resquício de desenvolvimento. O requisito é `crontab(hour=0,
   minute=0)` (00:00 UTC). Correção de uma linha em `config/settings.py`.
2. **Import não utilizado** `from google.protobuf import timestamp` em
   `conversations/services.py` — resíduo de autocomplete, sem efeito, a remover.
3. **`register_message` também grava a mensagem do assistente**, com o
   papel passado por parâmetro. Funciona, mas o nome mente. Extrair
   `register_assistant_message` deixaria as duas intenções explícitas.
4. **A resposta do assistente reusa o `timestamp` da mensagem do cliente.**
   Preserva a ordenação do histórico, mas não reflete o momento real da resposta.
   O correto seria `timezone.now()` no momento da persistência.
5. **`XMLPropertyImporter` e `APIRestPropertyImporter` são esqueletos.** O
   desafio não pede a implementação; eles existem para demonstrar que o ponto de
   extensão é real.
6. **Sem lock por conversa.** Coberto pela premissa do desafio; num sistema real,
   lock no Redis com TTL por `conversation_id` seria o próximo passo (seção 5).
7. **Sem retry configurado nas tasks.** Uma falha de rede na chamada ao LLM cai
   direto no fallback. `autoretry_for` com backoff exponencial seria o próximo
   incremento, tomando cuidado para não interagir mal com o debounce.
