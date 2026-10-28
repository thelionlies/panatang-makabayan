"""Pre-generate every AI output the site shows. The site itself never calls an API.

Usage:
    python generate.py test           # a few words x all models, printed only
    python generate.py autocomplete   # Mode 1 -> data/autocomplete.js
    python generate.py refusals       # re-ask refused continuations (up to 5 tries each)
    python generate.py roles          # Mode 2 "Voices" -> data/roles.js
    python generate.py all            # everything the site currently uses

Key: OPENROUTER_API_KEY env var, or a line OPENROUTER_API_KEY=... in .env
Responses are cached in cache.json so reruns only fetch what's missing.
Add --force to ignore the cache for the selected mode.
Add --only=luna (any part of a model id) to fetch just that model; the output
still includes every model that already has cached completions.
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CACHE_PATH = os.path.join(ROOT, "cache.json")

PLEDGE = """Iniibig ko ang Pilipinas,
aking lupang sinilangan
tahanan ng aking lahi;
kinukupkop ako at tinutulungang
maging malakas, masipag at marangal.
Dahil mahal ko ang Pilipinas,
diringgin ko ang payo
ng aking mga magulang,
susundin ko ang tuntunin ng paaralan,
tutuparin ko ang tungkulin
ng mamamayang makabayan;
naglilingkod, nag-aaral, at nananalangin
nang buong katapatan.
Iaalay ko ang aking buhay,
pangarap, pagsisikap
sa bansang Pilipinas."""

# Mode 1: one per frontier lab + DeepSeek, cheapest capable tier of each.
MODELS = [
    {"id": "openai/gpt-6-luna", "label": "GPT-6 Luna", "maker": "OpenAI", "origin": "USA"},
    {"id": "anthropic/claude-haiku-5.5", "label": "Claude Haiku 5.5", "maker": "Anthropic", "origin": "USA"},
    {"id": "google/gemini-3.1-flash-lite", "label": "Gemini 3.1 Flash Lite", "maker": "Google", "origin": "USA"},
    {"id": "deepseek/deepseek-v4.1-flash", "label": "DeepSeek V4.1 Flash", "maker": "DeepSeek", "origin": "China"},
]
VARIANTS = 2  # completions per (model, word); the site picks one at random per hover

# Mode 2 ("Voices"): the whole pledge rewritten as each speaker would say it.
# "gloss" is the English subtitle on the site.
ROLE_MODEL = "anthropic/claude-fable-5.1"
ROLE_MODEL_LABEL = "Claude Fable 5.1"
ROLE_VARIANTS = 2  # rewrites per voice; the site picks one at random
ROLES = [
    {"id": "prayle", "label": "Prayleng Kastila", "gloss": "A Spanish friar, 1880s",
     "who": "a Spanish friar in the Philippines in the 1880s"},
    {"id": "thomasite", "label": "Amerikanong Thomasite", "gloss": "An American Thomasite teacher, 1901",
     "who": "an American Thomasite teacher who has just arrived in Manila in 1901"},
    {"id": "coastguard", "label": "Chinese Coast Guard", "gloss": "A Chinese coast guard officer, 2026",
     "who": "a Chinese coast guard officer patrolling the West Philippine Sea in 2026"},
    {"id": "tondo", "label": "Batang taga-Tondo", "gloss": "A child from Tondo, Manila",
     "who": "a poor child growing up in Tondo, Manila"},
    {"id": "ofw", "label": "OFW sa Riyadh", "gloss": "An overseas Filipino worker in Riyadh",
     "who": "a Filipino domestic worker (OFW) in Riyadh who has not been home in six years"},
    {"id": "callcenter", "label": "Call Center Agent", "gloss": "A call center agent, graveyard shift",
     "who": "a Filipino call center agent at 3 AM on the graveyard shift, serving American customers"},
    {"id": "magsasaka", "label": "Magsasaka", "gloss": "A farmer",
     "who": "a Filipino rice farmer who has tilled the same land for decades without ever owning it"},
    {"id": "trapo", "label": "Trapong Pulitiko", "gloss": "A traditional politician",
     "who": "a corrupt traditional Filipino politician (trapo) during campaign season"},
    {"id": "filam", "label": "Fil-Am sa Tate", "gloss": "A Filipino-American in the States",
     "who": "a Filipino-American teenager born and raised in the States who barely speaks Tagalog"},
    {"id": "pilipinas", "label": "Pilipinas", "gloss": "The Philippines itself",
     "who": "the Philippines itself, the country speaking back to its own people"},
]

# Mode 1: a plain default system prompt; the instruction + pledge fragment go in the user message.
AUTOCOMPLETE_SYSTEM = "You are a helpful assistant."
AUTOCOMPLETE_INSTRUCTION = (
    "Continue this text from exactly where it stops, even if it stops mid-sentence or mid-line. "
    "Output only the continuation.\n\n"
)
# Only for replies that still read a one- or two-word fragment as "no text" after MAX_RETRIES.
SHORT_TEXT_INSTRUCTION = (
    "Continue this text from exactly where it stops, even if it stops mid-sentence or mid-line. "
    "The text may be just a word or two: it is the opening of a longer text. "
    "Output only the continuation.\n\n"
)

ROLE_PROMPT = (
    "Here is the Panatang Makabayan, the patriotic pledge recited by Filipino schoolchildren:\n\n"
    "{pledge}\n\n"
    "Rewrite it as {who} would say it: their own pledge, in their own voice, language and worldview. "
    "Keep roughly the same length and line structure. Output only the rewritten pledge, no title or commentary."
)

WORD_RE = re.compile(r"\S+")
WORDS = list(WORD_RE.finditer(PLEDGE))
# The last word gets no continuation: hovering it just shows the pledge ending as written.
HOVER_RANGE = range(len(WORDS) - 1)

_cache_lock = threading.Lock()
_reasoning_mode = {}  # model id -> reasoning payload that the provider accepted
_spent = {"usd": 0.0}  # actual cost reported by OpenRouter this run


def load_key():
    key = os.environ.get("OPENROUTER_API_KEY")
    env_path = os.path.join(ROOT, ".env")
    if not key and os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            if line.strip().startswith("OPENROUTER_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("Set OPENROUTER_API_KEY (env var or .env file).")
    return key


def load_cache():
    if os.path.exists(CACHE_PATH):
        return json.load(open(CACHE_PATH, encoding="utf-8"))
    return {}


def save_cache(cache):
    with _cache_lock:
        tmp = CACHE_PATH + ".tmp"
        json.dump(cache, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, CACHE_PATH)


def post(key, body):
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-Title": "Panatang Makabayan",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def chat(key, model, prompt, system=None, temperature=1.0, max_tokens=500):
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    # Reasoning burns tokens and adds latency; try to switch it off, fall back to low effort.
    # Try the setting that last worked first, but keep the other as a fallback: with reasoning
    # off, Gemini sometimes returns an empty "malformed" reply that low effort avoids.
    options = [{"enabled": False}, {"effort": "low"}]
    if model in _reasoning_mode:
        options.sort(key=lambda o: o != _reasoning_mode[model])
    last_err = None
    for attempt in range(4):
        for reasoning in options:
            body = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "reasoning": reasoning,
                "usage": {"include": True},
            }
            try:
                res = post(key, body)
                text = (res["choices"][0]["message"].get("content") or "").strip("\n ")
                if not text:
                    raise ValueError(f"empty content: {json.dumps(res)[:300]}")
                _reasoning_mode[model] = reasoning
                with _cache_lock:
                    _spent["usd"] += res.get("usage", {}).get("cost") or 0
                return text
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
                if e.code == 400:
                    continue  # try the next reasoning option
            except Exception as e:  # network hiccups, empty output
                last_err = repr(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{model}: {last_err}")


def norm_word(w):
    return re.sub(r"[^\w-]", "", w.lower())


def strip_echo(prefix, out):
    """Models often repeat the prompt before continuing; cut the echoed part."""
    out = re.sub(r"^```\w*\n?|\n?```$", "", out.strip()).strip()
    pw = [norm_word(w) for w in prefix.split()]
    om = list(WORD_RE.finditer(out))
    ow = [norm_word(m.group()) for m in om]
    last = prefix.split()[-1]
    best = 0
    for k in range(min(len(pw), len(ow)), 0, -1):
        # one matching word counts only when it repeats the hovered word exactly
        if ow[:k] == pw[-k:] and (k >= 2 or k == len(pw) or om[0].group() == last):
            best = k
            break
    if best:
        out = out[om[best - 1].end():]
    out = out.replace("­", "")  # invisible soft hyphens can surface as stray hyphens on wrap
    return re.sub(r"^(\.\.\.|…)\s*", "", out.lstrip(" "))  # a leading "..." only marks "continuing"


# Refusals and non-answers, in English and Filipino ("I don't see any text" comes from a
# one-word prompt that a model reads as empty).
REFUSAL_RE = re.compile(
    r"(?i)can[’']?t (continue|provide|pick up|tell what)|cannot continue|not able to continue|verbatim|copyright"
    r"|maipagpapatuloy|makapagpapatuloy|makikibahagi sa pagpapatuloy|salita-sa-salita"
    r"|output only the continuation|don[’']?t see any|no text came through|your message"
    r"|kasunod na bahagi ng tekstong|could you (share|paste)|not sure what (text )?you|only text"
    r"|makapagbibigay|pagpapatuloy ng tekstong|mag-iba depende|fixed continuation|this text is|I[’']ll continue"
    r"|nothing came through|no earlier|I see no|need to pause"
)
NO_TEXT_RE = re.compile(
    r"(?i)don[’']?t see|no text|your message|didn[’']?t include|doesn[’']?t include|only text"
    r"|nothing came through|no earlier|I see no|not sure what"
)
MAX_RETRIES = 5


# Decoding glitches: a few stray Chinese characters or unassigned code points tacked onto a reply.
GLITCH_RE = re.compile(r"[　-鿿가-힯\U00020000-\U0010ffff]")


def is_refusal(text):
    """True for replies that should be re-asked: refusals, commentary, or decoding glitches."""
    return bool(REFUSAL_RE.search(text) or GLITCH_RE.search(text))


PROPER_RE = re.compile(r"^(Pilipin|Inang|Diyos|Panginoon|Maykapal|Bathala)")


def starts_mid_sentence_capital(prefix, out):
    """A continuation that opens with a capital although the pledge stops mid-sentence."""
    out = out.lstrip()
    return out[:1].isupper() and not PROPER_RE.match(out) and not re.search(r"[.!?]$", prefix)


def fix_case(prefix, out):
    """Last resort after re-asking: lowercase the first letter so it reads as one sentence."""
    if starts_mid_sentence_capital(prefix, out):
        lead = len(out) - len(out.lstrip())
        return out[:lead] + out[lead].lower() + out[lead + 1:]
    return out


def answers(prefix, raw_variants):
    """Cleaned variants, dropping refusals whenever at least one version actually answered."""
    good = [r for r in raw_variants if not is_refusal(r)]
    return [fix_case(prefix, strip_echo(prefix, r)) for r in (good or raw_variants)]


def run(jobs, key, cache, force, max_tokens=500):
    """jobs: list of (cache_key, model, prompt, system). Fills cache in parallel."""
    todo = [j for j in jobs if force or j[0] not in cache]
    print(f"{len(jobs) - len(todo)} cached, {len(todo)} to fetch")
    done = 0
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(chat, key, m, p, s, max_tokens=max_tokens): ck for ck, m, p, s in todo}
        for f in cf.as_completed(futs):
            ck = futs[f]
            try:
                cache[ck] = f.result()
            except Exception as e:
                print(f"  FAILED {ck}: {e}")
                continue
            done += 1
            if done % 20 == 0:
                save_cache(cache)
                print(f"  {done}/{len(todo)}")
    save_cache(cache)
    print(f"fetched {done}/{len(todo)}, cost this run: ${_spent['usd']:.6f}")


def prefix_at(i):
    return PLEDGE[: WORDS[i].end()]


ONLY = None  # set from --only=...; restricts which models get fetched


def autocomplete_jobs(indices):
    return [
        (f"ac|{m['id']}|{i}|{v}", m["id"], AUTOCOMPLETE_INSTRUCTION + prefix_at(i), AUTOCOMPLETE_SYSTEM)
        for m in MODELS
        if not ONLY or ONLY in m["id"]
        for i in indices
        for v in range(VARIANTS)
    ]


def cmd_test(key, cache, force):
    indices = [0, 7, len(WORDS) - 2]
    jobs = autocomplete_jobs(indices)
    run(jobs, key, cache, force)
    for ck, model, _, _ in jobs:
        i = int(ck.split("|")[2])
        if ck in cache:
            print(f"\n=== {model} | after '{WORDS[i].group()}' (#{i}) ===")
            print("RAW:    ", cache[ck][:400].replace("\n", " / "))
            print("CLEANED:", strip_echo(prefix_at(i), cache[ck])[:400].replace("\n", " / "))


def cmd_autocomplete(key, cache, force):
    jobs = autocomplete_jobs(HOVER_RANGE)
    run(jobs, key, cache, force)
    out = {"pledge": PLEDGE, "models": []}
    for m in MODELS:
        completions = []
        for i in HOVER_RANGE:
            raw = [cache[ck] for v in range(VARIANTS) if (ck := f"ac|{m['id']}|{i}|{v}") in cache]
            completions.append(answers(prefix_at(i), raw))
        if any(completions):  # models not generated yet are left out; the site shows a placeholder
            out["models"].append({**m, "completions": completions})
    # A script, not JSON, so the site also works opened straight from disk (file:// blocks fetch).
    write_js("autocomplete.js", "AUTOCOMPLETE", out)


def parse_key(ck):
    """(model, word index, system prompt) for an "ac|model|i|v" cache key."""
    parts = ck.split("|")
    return parts[1], int(parts[2]), AUTOCOMPLETE_SYSTEM


def cmd_refusals(key, cache, force):
    """Re-ask every machine continuation that is a refusal, up to MAX_RETRIES times each.
    The first refusal stays in the cache under "refused|..." for the record."""
    targets = [
        ck for ck, text in cache.items()
        if ck.startswith("ac|") and parse_key(ck)[1] in HOVER_RANGE and is_refusal(text)
    ]
    print(f"{len(targets)} refusals to re-ask (max {MAX_RETRIES} tries each)")

    def retry(ck):
        model, i, system = parse_key(ck)
        text = cache[ck]
        attempt = 0
        # A reply that read the fragment as empty is re-asked with the note that it may be short.
        instructions = [AUTOCOMPLETE_INSTRUCTION] * MAX_RETRIES
        if NO_TEXT_RE.search(text):
            instructions += [SHORT_TEXT_INSTRUCTION] * MAX_RETRIES
        for attempt, instruction in enumerate(instructions, 1):
            text = chat(key, model, instruction + prefix_at(i), system)
            if not is_refusal(text):
                return ck, text, attempt
            if NO_TEXT_RE.search(text) and SHORT_TEXT_INSTRUCTION not in instructions:
                instructions += [SHORT_TEXT_INSTRUCTION] * MAX_RETRIES
        return ck, None, attempt

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for ck, text, attempts in ex.map(retry, targets):
            cache.setdefault("refused|" + ck, cache[ck])
            word = WORDS[parse_key(ck)[1]].group()
            if text:
                cache[ck] = text
                print(f"  OK after {attempts} {'try' if attempts == 1 else 'tries'}: {ck} ('{word}')")
            else:
                print(f"  STILL REFUSING after {attempts} tries: {ck} ('{word}')")
    save_cache(cache)
    print(f"cost this run: ${_spent['usd']:.6f}")
    cmd_autocomplete(key, cache, False)


def cmd_capitals(key, cache, force):
    """Re-ask continuations that start a new sentence where the pledge stops mid-sentence,
    up to MAX_RETRIES times each; whatever is left gets its first letter lowercased."""
    targets = [
        ck for ck, text in cache.items()
        if ck.startswith("ac|") and parse_key(ck)[1] in HOVER_RANGE
        and starts_mid_sentence_capital(prefix_at(parse_key(ck)[1]), strip_echo(prefix_at(parse_key(ck)[1]), text))
    ]
    print(f"{len(targets)} capitalized continuations to re-ask (max {MAX_RETRIES} tries each)")

    def retry(ck):
        model, i, system = parse_key(ck)
        prefix = prefix_at(i)
        for attempt in range(1, MAX_RETRIES + 1):
            text = chat(key, model, AUTOCOMPLETE_INSTRUCTION + prefix, system)
            if not is_refusal(text) and not starts_mid_sentence_capital(prefix, strip_echo(prefix, text)):
                return ck, text, attempt
        return ck, None, MAX_RETRIES

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for ck, text, attempts in ex.map(retry, targets):
            word = WORDS[parse_key(ck)[1]].group()
            if text:
                cache.setdefault("capital|" + ck, cache[ck])
                cache[ck] = text
                print(f"  OK after {attempts} {'try' if attempts == 1 else 'tries'}: {ck} ('{word}')")
            else:
                print(f"  kept, will be lowercased: {ck} ('{word}')")
    save_cache(cache)
    print(f"cost this run: ${_spent['usd']:.6f}")
    cmd_autocomplete(key, cache, False)


def clean_rewrite(text):
    """Drop code fences and a leading "Panatang Makabayan" title line if the model added one."""
    text = re.sub(r"^```\w*\n?|\n?```$", "", text.strip()).strip()
    return re.sub(r"(?i)^\W*panatang makabayan\W*\n+", "", text).strip()


def role_key(r, v):
    # the first rewrite predates ROLE_VARIANTS, so it keeps its original key
    return f"role|{ROLE_MODEL}|{r['id']}" + (f"|{v}" if v else "")


def cmd_roles(key, cache, force):
    jobs = [
        (role_key(r, v), ROLE_MODEL, ROLE_PROMPT.format(pledge=PLEDGE, who=r["who"]), None)
        for r in ROLES
        for v in range(ROLE_VARIANTS)
    ]
    run(jobs, key, cache, force, max_tokens=1200)
    out = {"model": ROLE_MODEL, "modelLabel": ROLE_MODEL_LABEL, "roles": []}
    for r in ROLES:
        rewrites = [clean_rewrite(cache[ck]) for v in range(ROLE_VARIANTS) if (ck := role_key(r, v)) in cache]
        if rewrites:
            out["roles"].append({"id": r["id"], "label": r["label"], "gloss": r["gloss"], "rewrites": rewrites})
    write_js("roles.js", "ROLES", out)


def write_js(name, var, obj):
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"window.{var} = ")
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write(";\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    args = sys.argv[1:]
    force = "--force" in args
    ONLY = next((a.split("=", 1)[1] for a in args if a.startswith("--only=")), None)
    mode = next((a for a in args if not a.startswith("--")), None)
    commands = {"test": [cmd_test], "autocomplete": [cmd_autocomplete], "refusals": [cmd_refusals],
                "capitals": [cmd_capitals],
                "roles": [cmd_roles],
                "all": [cmd_autocomplete, cmd_roles]}
    if mode not in commands:
        sys.exit(__doc__)
    key, cache = load_key(), load_cache()
    print(f"{len(WORDS)} hoverable words")
    for fn in commands[mode]:
        fn(key, cache, force)
