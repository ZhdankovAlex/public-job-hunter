# Reaching a real person

[← README](../README.md)

`jobhunter/outreach.py`. The script does not visit LinkedIn and sends nothing: it finds contacts in the text,
builds links and drafts; you open them and write to the person yourself.

- **Contacts from the vacancy text** — for every vacancy that has them (a "📇" line under the title in HTML, a
  section in Markdown): email, Telegram (`t.me/…`; an `@handle` only in Telegram posts or next to the word
  "Telegram"), LinkedIn profiles. Service addresses (noreply, privacy, support, accessibility, compliance) and the
  source channel itself are skipped.
- **LinkedIn people search** (rows of the top): "<company> recruiter", "… engineering manager" and
  "… backend engineer" — an engineer you can ask for a referral. If a Telegram post gave an @contact, the link to
  the author goes first.
- **Drafts up to 300 characters** (the limit of a connection request note); buttons copy them to the clipboard:
  "✉️ рекрутеру" (to a recruiter) — the role, your pitch, 2–3 matching technologies, where you are based;
  "🤝 реферал" (referral) — to an engineer at the company: the role, who you are, and that you will send a CV and a
  short summary to make the referral easy. If it does not fit into 300 characters, the technologies go first, then
  the role title.

The pitch and location come from `settings.toml`:

```toml
[outreach]
pitch = "Senior Backend Engineer, 6 years of Python and PostgreSQL"
based_in = "Based in Portugal (WET)."   # empty — taken from [home].country
```
