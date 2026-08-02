# Demo script (5–10 minutes)

A beat sheet for the walkthrough video. The audience is the client: a financial-services
company that today keeps hundreds of documents on a shared drive. Lead with their problem,
not with the stack.

**Before recording**

```bash
make clean && make up && make seed     # clean state, six sample documents
```

Have open: the UI (`:3000`), a terminal, and an MCP client (Claude Code or Claude Desktop)
already configured — see the README for the `claude mcp add` command. Add the MCP server
*on camera* if you want to show how easy connecting is.

---

## 1 · The problem (~45s) — no screen, just talk

> Today your team finds information by remembering which folder a document lives in. That
> works until it doesn't: the policy gets renamed, the person who filed it leaves, and the
> answer is in section 4.2 of a 40-page PDF that nobody is going to open.
>
> What we've built lets an employee ask a question in plain language and get a precise
> answer with a citation — and it lets *your* AI assistant do the same, because the
> knowledge base is exposed as a set of tools any AI agent can use.

Name the three pieces once, and only once: **a place to manage documents**, **a pipeline
that makes them searchable**, and **an MCP server the AI connects to**.

## 2 · Managing documents (~90s) — the UI

- Show the list: filenames, tags, status, passage counts.
- **Upload a PDF**, type two tags, submit. Point at the status going
  `pending → processing → ready`, and the passage count appearing.
  > "Behind that, we've extracted the text, split it into meaningful sections, and turned
  > each one into a numerical fingerprint the AI can search by meaning."
- **Upload the same file again.** The UI says *"already in the knowledge base; added new
  tag(s): …"*.
  > "Re-uploading is something people do constantly. It doesn't create a second copy — it
  > just merges whatever new tags you added. That matters, because duplicates are what
  > make an AI give the same answer twice and contradict itself."
- Use the **tag filter**, then **delete** a document.
  > "Deleting removes it everywhere, including from what the AI can see. There's no stale
  > copy left behind."

## 3 · How a document becomes searchable (~60s) — one slide or the README diagram

Keep it conceptual. Three sentences:

1. **We split on the document's own structure** — sections and subsections, not arbitrary
   page cuts — so each piece is about one topic.
2. **Each piece carries its section heading**, which is both what makes it findable and
   what the AI cites back to you.
3. **We search two ways at once**: by meaning, and by exact words. Meaning finds *"how do I
   escalate a suspicious payment"*; exact words find *"MiFID II"* and *"EUR 10,000"*.
   Results are combined so a passage has to do well on both to reach the top.

## 4 · The MCP server (~3–4 min) — the centrepiece

Start in the terminal:

```bash
make mcp-check
```

Point at the first two lines.
> "Without the key, the endpoint refuses the request. This is the door — nothing gets to
> your documents without a credential."

Then the tool list.
> "These seven tools are what the AI sees. Tool design is most of the work in a project
> like this: the AI decides what to do purely from these names and descriptions."

Now switch to the agent and run a real conversation:

1. **"What do we have on compliance?"**
   → agent calls `list_tags` / `list_documents`. Show it discovering the tag vocabulary
   rather than guessing.

2. **"How quickly do I have to report a suspicious transaction?"**
   → `search`. Read the answer aloud *with its citation* — filename and section.
   > "That's from the AML policy, section 4.1. It's not the model's general knowledge —
   > it's your document, and it can show you exactly where it came from."

3. **"Only look at HR material — how much annual leave can I carry over?"**
   → `search_by_tag`. Point out that it checked the tags exist first.

4. **A follow-up on one document:** *"and what does the onboarding guide say about
   probation?"* → `search_by_document`.
   > "Three separate search tools, deliberately. If narrowing were an optional setting, the
   > AI would forget to set it and quietly answer from the wrong part of the library. Making
   > it a distinct tool with a required input makes that decision explicit."

5. **Ask something the documents don't cover** — e.g. *"what's our crypto custody policy?"*
   → show the agent saying the knowledge base doesn't cover it, instead of inventing an
   answer.
   > "This is the behaviour that matters most in a regulated business. When we have no
   > answer, the tools tell the AI what to try next — and if there's still nothing, it says
   > so rather than guessing."

## 5 · Running it (~45s)

```bash
make up
```

> "The whole system — database, search index, API, MCP server and the web app — starts with
> one command. Nothing is configured by hand, and the same images run in the cloud."

Mention secrets are environment variables, never in the repository.

## 6 · Honest limitations (~30s)

Pick two or three from the README. Naming real limits builds more trust than claiming none:

- No reranking step yet — the highest-value next improvement for answer quality.
- Scanned PDFs need OCR; today they're rejected with a clear message rather than indexed as
  empty.
- One shared key today, so every agent sees everything. Per-user permissions, so an agent
  only retrieves what that employee is allowed to see, is the first thing to build for a
  real rollout.

## 7 · Close (~15s)

> Documents in through a simple web page; grounded, cited answers out through any AI
> assistant you already use. Everything the AI says can be traced back to a specific
> section of a specific document — which is the part that makes this usable in a regulated
> business.

---

### Timing

| Section | Target |
| --- | --- |
| 1 Problem | 0:45 |
| 2 Document management | 1:30 |
| 3 How it works | 1:00 |
| 4 MCP server | 3:30 |
| 5 Running it | 0:45 |
| 6 Limitations | 0:30 |
| 7 Close | 0:15 |
| **Total** | **~8:15** |
