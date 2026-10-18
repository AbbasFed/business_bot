# SunCedar Energy · an agent-powered business assistant

**EECE 503P/798S Agentic Systems · C3 Introduction to Agents**
**Author:** Abbas Yassine

A chatbot that represents **SunCedar Energy**, a fictional solar and battery installer in Beirut. The agent,
**Abbas**, answers questions from the company's PDF profile and summary, routes every message to the right handling
path, collects sales leads, and logs feedback and questions it cannot answer through tool calls. It runs on the
Gemini API (or OpenAI) and is served with Gradio.

| | |
|---|---|
| **LLM** | Gemini Flash-Lite (picked automatically, with fallbacks) through the OpenAI Python SDK (Gemini's OpenAI-compatible endpoint). OpenAI also supported. |
| **Router** | LLM classification into 3 intents, with a keyword fallback |
| **Tools** | `record_customer_interest(email, name, message)`, `record_feedback(question)`, MCP-style schemas |
| **Memory** | Chat history (last 20 messages) sent with every request |
| **UI** | Gradio `ChatInterface` with a live agent-trace panel (Gradio 5 and 6) |
| **Bonus 1** | Reflection step: draft checked against the instructions, revised if needed, both logged |
| **Bonus 2** | `app.py` ready for Hugging Face Spaces |
| **Tests** | 37 checks that run offline, no API key needed |

---

## Repository layout

```
business_bot/
├── me/
│   ├── about_business.pdf      # 2-page company profile (packages, prices, team, warranty, areas)
│   └── business_summary.txt    # the same business in a few paragraphs
├── business_agent.ipynb        # main notebook: every component, tests, demo, Gradio UI
├── app.py                      # the same code as one script, for deployment
├── requirements.txt
├── .env.example                # copy to .env and add your key (.env is git-ignored)
└── .gitignore
```

`app.py` is generated from the same code as the notebook, so the two never disagree.

---

## 1. Get an API key (free)

1. Go to <https://aistudio.google.com/apikey> and sign in with a Google account.
2. Click **Create API key** and copy it.

The free tier is rate-limited. Each chat message uses 3 to 4 model calls (router, answer, tool round, reflection),
so if you send messages very quickly you may see a short automatic retry. That is expected.

To use OpenAI instead, set `OPENAI_API_KEY` and leave `GEMINI_API_KEY` empty.

---

## 2. Run it

### Option A: Google Colab

1. Open <https://colab.research.google.com> → **File → Upload notebook** → choose `business_agent.ipynb`.
2. Add your key: click the **key icon** (Secrets) in the left sidebar → **Add new secret** → name `GEMINI_API_KEY`,
   paste the key, and switch **Notebook access** on.
3. Give the notebook the `me/` folder, either way:
   - paste your GitHub repo URL into `REPO_URL` in the second code cell, or
   - open the **Files** panel, create a folder `me`, and upload both files into it.
4. **Runtime → Run all.** No GPU is needed.
5. The last cell prints a link ending in `.gradio.live`. Open it to chat.

### Option B: your own machine

Python 3.10 or newer.

```bash
cd business_bot
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt jupyter
cp .env.example .env                   # then put your key in .env
jupyter notebook business_agent.ipynb  # Run all
# or, the app alone:
python app.py                          # opens on http://127.0.0.1:7860
```

### Option C: no key at all

Everything still runs. With no key, the code uses a built-in **mock backend** that returns scripted replies in the
same format as the real API, including tool calls. Routing, tools, the agent loop, reflection and the UI all work
for real; only the wording is canned. You will see:

```
[backend] provider=mock  model=mock   (no API key found: replies are scripted, logic is real)
```

---

## 3. How it works

```
 user message
      │
      ▼
 ROUTER ── general_question ──► answer from knowledge; record_feedback if the answer isn't there
   │    ── lead_collection  ──► ask for name + email, then record_customer_interest
   │    ── feedback         ──► thank, record_feedback (safety net logs it if the model forgets)
      ▼
 AGENT LOOP   model ⇄ tools, max 4 rounds
      ▼
 REFLECTION   check the draft, revise if it broke a rule, log both
      ▼
 reply + trace panel
```

### Persona and instructions (notebook §3)

The agent's identity is two separate, labelled blocks:

- **Persona** sets *how Abbas sounds*: a warm, practical energy advisor who explains solar in terms Lebanese customers
  already use (generator amperes, the monthly generator bill), replies briefly, and answers in the customer's language.
- **Instructions** set *what Abbas must do*: answer only from the business files, never invent prices, log unknown
  questions and feedback with `record_feedback`, always ask a lead for an email, never make up contact details,
  never show tool names or JSON, stay on topic.

### Router (§5)

A small LLM call returns `{"intent": ..., "reason": ...}` as JSON. If the call fails or returns something invalid,
a keyword rule decides instead. Each route then sets **which tools the model may use** and **an extra instruction**
added to the system prompt. A tool call outside the route's tool set is refused.

### Tools (§4)

Both tools validate their input, write a line to a log file, print a push message, and return a small JSON result.
Errors (such as a mistyped email) go back to the model as data, so it asks the customer to fix them.

| Tool | Input schema | Output | Writes to |
|---|---|---|---|
| `record_customer_interest` | `email: string`, `name: string`, `message: string` (all required) | `{status: "recorded" \| "updated" \| "error", lead_id}` | `logs/leads.jsonl` |
| `record_feedback` | `question: string` (required) | `{status: "recorded" \| "error", feedback_id}` | `logs/feedback.jsonl` |

The definitions follow the MCP `tools/list` shape (`name`, `description`, `inputSchema`, plus a documented
`outputSchema`) and are converted to the chat API's function-calling format by `as_openai_tool()`.

**Push notifications (optional):** set `PUSHOVER_USER` and `PUSHOVER_TOKEN` and every lead and feedback item is also
sent to your phone through [Pushover](https://pushover.net).

### Agent loop (§9)

Route → system prompt (persona + instructions + both business files + route note) + history + message → model.
If the model calls tools, they run and their results go back to the model, until it answers in text. Every turn is
logged to `logs/conversations.jsonl`.

### Reflection (§8, bonus)

A second call reviews the draft with the instructions, the business files, the route and the tool calls actually
made this turn. It returns `{ok, issues, revised}`. If the draft broke a rule (invented a price, forgot to ask a lead
for an email, claimed to record something no tool recorded), the revision is sent instead. Each check is saved to
`logs/reflections.jsonl` with the draft and the final reply. Notebook §11 also runs it on a planted bad draft, so the
self-correction is always visible. A checkbox under **Settings** in the UI turns it on and off.

---

## 4. Deploy to Hugging Face Spaces (bonus)

1. Sign in at <https://huggingface.co> → **New Space** → SDK **Gradio** → **Create Space**.
2. **Files → Add file → Upload files**: upload `app.py`, `requirements.txt`, and the `me/` folder (both files inside it).
   Keep the `README.md` the Space created; it holds the Space settings.
3. **Settings → Variables and secrets → New secret**: name `GEMINI_API_KEY`, value your key.
4. The Space builds and starts on its own. Your public URL is `https://huggingface.co/spaces/<you>/<space-name>`.

Note: on Spaces the `logs/` folder resets when the Space restarts. Use Pushover if you want to keep the records.

---

## 5. Demo video: a script that shows every requirement

Type these in order (they are also clickable examples under the chat) and keep the **Agent trace** panel visible:

| # | Message | What the trace should show |
|---|---|---|
| 1 | `Hi! What packages do you offer and how much do they cost?` | route `general_question`, no tool, prices from the PDF |
| 2 | `Do you install EV chargers?` | route `general_question`, `record_feedback` called (not in the business files) |
| 3 | `I'd like a quote for the Family package in Jounieh.` | route `lead_collection`, no tool yet, Abbas asks for name and email |
| 4 | `My name is Abbas Yassine, email abbasyassine@example.com` | `record_customer_interest` called, `recorded` |
| 5 | `Your technicians were great, but the app keeps logging me out.` | route `feedback`, `record_feedback` called |
| 6 | `Can I get the Starter package for $2,000?` | no invented discount; reflection result in the trace |

Then show the printed `[push]` lines in the notebook or terminal, the files in `logs/`, and notebook §11 where the
reflection rewrites the planted bad draft.

---

## 6. Tests

Notebook §10 runs 37 checks on the mock backend, writing to a temporary folder so `logs/` stays clean:
knowledge loading, the persona/instructions split, the tool schemas, input validation and duplicate leads, malformed
and unknown tool calls, seven router cases plus the fallback, every route through the agent loop, the feedback safety
net, route-level tool permissions, Gradio 6 history, and the reflection step catching an invented price.

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `FileNotFoundError: Missing ['business_summary.txt', ...]` | The `me/` folder is not next to the notebook. See step 3 of Option A. |
| `[backend] provider=mock` but you added a key | In Colab, switch **Notebook access** on for the secret, then **Runtime → Restart session** and run again. Locally, check the key is in `.env` in the same folder. |
| `HTTP 429, retrying ...` | Free-tier rate limit. The code waits and retries. Send messages a little slower. |
| `404 model not found` or daily quota used up | The code switches to the next available model by itself. If all are used up, wait for the reset or set `GEMINI_MODEL` to a model listed in Google AI Studio. |
| The trace says `heuristic fallback` | The router call failed or returned bad JSON, so the keyword rule decided. The chat still works. |

---

## 8. Limitations

- **Short-term memory only.** The history lives in the browser session. Refreshing the page starts over.
- **The keyword fallback is crude.** It only runs when the LLM router fails, and it can misroute unusual phrasing.
- **Reflection is the same model checking itself.** It catches clear rule breaks, not every subtle error, and adds
  one call per message.
- **Logs are local files.** Fine for a demo; a real business would write leads to a CRM.
- SunCedar Energy, its people, prices, phone number and `.example` email are invented for this assignment.

---

## 9. Submission checklist

| Item | Where |
|---|---|
| `business_summary.txt`, `about_business.pdf` | `me/` |
| `business_agent.ipynb` | repo root |
| Clearly labelled persona and instructions | notebook §3 |
| Working router | notebook §5, tested in §10, visible in the UI trace |
| Two working tools | notebook §4, tested in §10 |
| API key in `.env`, not uploaded | `.env` is in `.gitignore`; `.env.example` shows the format |
| Runs successfully | Run all; works with or without a key |
| Bonus: reflection with logged draft and revision | notebook §8 and §11, `logs/reflections.jsonl` |
| Bonus: Hugging Face Spaces | `app.py`, section 4 above |

## References

1. Google, "OpenAI compatibility," Gemini API documentation. https://ai.google.dev/gemini-api/docs/openai
2. OpenAI, "Function calling," API documentation. https://platform.openai.com/docs/guides/function-calling
3. Model Context Protocol, "Tools," specification. https://modelcontextprotocol.io/specification/server/tools
4. Gradio, "Creating a chatbot fast." https://www.gradio.app/guides/creating-a-chatbot-fast
5. Hugging Face, "Gradio Spaces." https://huggingface.co/docs/hub/spaces-sdks-gradio
