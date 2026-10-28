# Panatang Makabayan

An experiment in machine continuation, built around the *Panatang Makabayan*, the pledge recited by Filipino schoolchildren.

**Live site:** https://thelionlies.github.io/panatang-makabayan/

## What it does

- **Machines.** Hover over any word of the pledge. The text is cut there, and a language model writes the rest. Four models are available: GPT-6 Luna, Claude Haiku 5.5, Gemini 3.1 Flash Lite and DeepSeek V4.1 Flash.
- **Voices.** Pick a speaker, such as a Spanish friar, an OFW in Riyadh or the country itself, and read the whole pledge as Claude Fable 5.1 imagined that person would say it.

No AI runs on the site. Every output was generated once, in advance, through [OpenRouter](https://openrouter.ai), and the page only displays it. The prompts and settings are listed on the site's **Notes** page.

## Files

| Path | What it is |
|---|---|
| `index.html`, `style.css`, `app.js` | The static site |
| `data/autocomplete.js` | Machine continuations: 62 words × 4 models × 2 versions |
| `data/roles.js` | Voice rewrites: 10 voices × 2 versions |
| `assets/layon.png` | Author portrait |
| `generate.py` | The script that produced everything in `data/` |

## Running it locally

Open `index.html` in a browser. No server or build step is needed.

## Regenerating the data

Needs Python 3.8+ and an OpenRouter API key. No extra packages are required.

1. Create a file named `.env` in the project folder containing:
   ```
   OPENROUTER_API_KEY=sk-or-...
   ```
   `.env` is gitignored.
2. Run:
   ```bash
   python generate.py test          # a few calls, printed only
   python generate.py all           # machine continuations and voice rewrites
   python generate.py refusals      # re-ask any continuation that came back as a refusal
   ```

Responses are cached in `cache.json` (also gitignored), so reruns only fetch what is missing. Add `--force` to fetch everything again.

## Credits

Made by **Láyon** ([thelionlies.github.io/layon](https://thelionlies.github.io/layon/)) for *Mga Sigaw ng Bayan Kong Sawi*, Santelmo Pambansang Hámon sa Pagtula 2026.

Made with AI.
