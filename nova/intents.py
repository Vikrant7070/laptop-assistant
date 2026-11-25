"""What the user means: every regex that reads the user's own words lives here (roadmap R14).

Sleep, stop, yes/no, "keep going", "watch me", corrections, "show me my screen"... used to be compiled inside
the app modules that react to them. One home makes them easy to find, test and keep apart from the regexes that
read a model's answer (those stay where they are used: conversation.DONE_CLAIM_RE and friends).

`match(kind, text)` tests one intent by its name in KINDS. tests/routing.jsonl holds golden cases
(`"kind": "intent"`) for each of them, near-misses included. The callers still add their own limits (a short
reply only, a word count, a pending question), so a match here is "the words say it", not "act on it".

The patterns moved here unchanged; renaming one is free, changing one changes how Nova hears people.
"""
import re


# --- Talking to her: sleep, stop, yes/no, and what to look at (from app/voice_io.py) ---


CAMERA = re.compile(
    r"\b(look at me|see me|how do i look|how am i looking|what am i (holding|wearing|doing)|"
    r"what('?s| is) (this|in my hand)|(through|use|open|check) (the |your |my )?(web)?cam(era)?|"
    r"can you see|do i look|am i smiling|what do you see)\b", re.I)


SCREEN = re.compile(
    r"\b(my screen|on (the |my )?screen|this screen|screen ?shot of|look at (this|my) (window|page|tab|error|code)|"
    r"read (this|that|the) (error|message|page)|what('?s| is) this error|this error|what am i looking at|"
    r"what('?s| is) open|what do you see on|"
    r"(any |a |some )?(suggestions?|tips?|ideas?)\b[^.?!]{0,30}\b(screen|working on|doing|this)|"
    r"what should i do next|help me with (this|what i'?m doing))\b", re.I)


# Text the user highlighted: "read this selected text", "what does the highlighted part say", "padho jo select kiya".
SELECTION = re.compile(r"\b(selected|selection|highlighted|highlight|select kiya|select ki)\b", re.I)


# ...and more than reading it out: then the brain gets the text and does the rest.
READ_MORE = re.compile(r"\b(summar|translat|explain|mean|fix|rewrite|correct|improve|check|reply|answer|why|"
                       r"short|simplif|convert|count|search|google|save|note|email|type|paste|samjha|matlab)", re.I)


# "open youtube and search lofi music" / "search lofi on youtube": a search on YouTube, opened by code at its results
# page (QA 2026-11-25: the brain clicked the search box, or went through Google, and stalled both times).
YOUTUBE_SEARCH = re.compile(
    r"^\W*(?:(?:please|nova|can you)\W+)*(?:open|go to)\s+youtube\s+(?:and\s+)?"
    r"(?:search|find|look up|look for)\s+(?:for\s+)?(?P<q>[^?!]+?)\W*$"
    r"|^\W*(?:(?:please|nova|can you)\W+)*(?:search|find|look up|look for)\s+(?:for\s+)?(?P<q2>[^?!]+?)\s+on\s+youtube\W*$",
    re.I)


def youtube_query(text):
    """The words to search YouTube for, or None."""
    m = YOUTUBE_SEARCH.search(text or "")
    return ((m.group("q") or m.group("q2")).strip() or None) if m else None


VIDEO = [  # clear video-player requests, done directly (the small model mixed up video vs window full screen)
    (re.compile(r"\b(exit|leave|close|get out of|come out of|stop|remove|minimi[sz]e)\b[^.?!]{0,25}full ?screen", re.I),
     "exit_fullscreen"),
    (re.compile(r"\bfull ?screen\b|\b(video|it)\b[^.?!]{0,20}\b(bigger|whole screen|entire screen)", re.I), "fullscreen"),
    (re.compile(r"\b(turn on|show|enable|switch on)\b[^.?!]{0,15}\b(subtitles|captions|cc)\b", re.I), "captions"),
    (re.compile(r"\btheat(er|re) mode\b", re.I), "theater"),
    (re.compile(r"\b(next video|skip (this|the) (video|song))\b", re.I), "next"),
]


FACT = [
    (re.compile(r"\b(what(?:'s| is) the time|what time|time (?:kya|kitna)|kitne baj|kitna baj|baje h|baj gaye|samay|"
                r"what(?:'s| is) the date|today'?s date|what day|aaj (?:kya|kaun)|tarikh|tareekh)", re.I), "get_datetime"),
    (re.compile(r"\b(battery|charging|charge (?:kitna|hai|left))", re.I), "get_system_status"),
    (re.compile(r"\b(internet|wi-?fi|network|net (?:chal|slow|band))", re.I), "network_info"),
]


# English and Hindi/Hinglish (Whisper writes Hindi in English letters - see voice.HINGLISH_HINT).
# Whisper often mishears it ("Noah got to sleep", "go too sleep"), so the verbs and the "to" are loose. Only short
# utterances count (wants_sleep), so "I can't go to sleep at night, any tips?" is a normal question.
SLEEP = re.compile(r"\b((go|got|goes|going|gotta|gonna|gone)( (to|too|two|2|ta))? ?sleep|sleep now|(go )?back to sleep|(you can|time to|now) sleep|"
                   r"sleep,? (nova|noah|now)|(nova|noah),? sleep|take a nap|nap time|go to bed|stop listening|"
                   r"good ?night|that'?s all|bye,? (nova|noah)|so ?jao|sunna band|shubh ratri|bas itna hi)\b", re.I)


# A bare "Nova, stop" / "ruko" / "bas" / "chup" said to her is a command, not a chat: it ends whatever she's doing
# (talking, a task) and goes nowhere near her brain. Before, it went to the brain, which chatted back ("I'm here,
# Vikrant - ready to help") - the opposite of stop. "Stop the music" has more words, so it still goes to the brain.
def just_stop_re(names):
    return re.compile(rf"^\W*(?:(?:hey|hi|ok|okay|arey)\W+)?(?:{names}\W+)?(?:please\W+)?"
                      r"(?:stop(?: it| that| talking| now)?|ru+c*k*h?o|roko|ruk jao|bas(?: karo)?|chup(?: raho| ho jao)?|"
                      r"enough|quiet|be quiet|shut up|that'?s enough)"
                      rf"(?:\W+(?:{names}|please|now|yaar))*\W*$", re.I)


YES = re.compile(r"^\W*(yes|yeah|yep|sure|ok(ay)?|do it|go ahead|allow( it)?|please do|haan?|han|ji|"
                 r"th?ee?k hai|kar ?do|karo|chalega|bilkul)\b", re.I)


NO = re.compile(r"^\W*(no|nope|don'?t|cancel|stop|never ?mind|nahin?|nai|mat|rehne do|ruko?|chhodo)\b", re.I)
# The WHOLE message only waves an open question card away ("never mind, drop that job"): the card closes and nothing
# else happens. "never mind, open Chrome instead" is not this - it goes on as a new request.
DROP_CARD = re.compile(r"^\W*(ok(ay)?\W+)?(never ?mind|cancel( (it|that|this))?|forget (it|that|about it)|drop (it|that|this)|"
                       r"leave it|rehne do|chhodo)(\W+(drop|cancel|forget|leave)( (it|that|this)( (job|one|question|"
                       r"task))?)?)?\W*$", re.I)


# --- Tasks (from app/conversation.py) ---


# "keep going" after she paused a long task: carry on with that task, don't start a new one (a skill matched
# "keep going" to work-mode once and opened VS Code and Gmail instead).
CONTINUE = re.compile(r"^\W*(please\s+)?(ok(ay)?\W+)?(keep going|carry on|continue|go on|resume|proceed|"
                      r"finish (it|that|the task)|jaari rakho|chalu rakho|aage badho|karte raho)\b", re.I)


# The whole message is "carry on" (app.resume picks the stopped task or finishes a cut-off reply in code). Stricter
# than CONTINUE: "continue playing the video" is a new request, not a resume.
_RESUME_PHRASE = (r"(keep going|carry on|continue|go on|resume|proceed|finish (it|that|the task)|"
                  r"you were saying( something)?|what were you saying|where were we|"
                  r"jaari rakho|chalu rakho|aage badho|karte raho|bolte raho)")
RESUME = re.compile(r"^\W*((please|ok(ay)?|yes|yeah|haan|nova|so)\W+)*" + _RESUME_PHRASE
                    + r"(\W+(" + _RESUME_PHRASE + r"|it|that|please|then|now|nova|with (it|that|the task)|"
                    r"from where you (left off|stopped)|where you (left off|stopped)))*\W*$", re.I)


# "two" / "the second one" / "2" as the answer to a numbered question (resume: which task?). No "do": "do it" is a yes.
_ORDINALS = {"1": 0, "one": 0, "first": 0, "ek": 0, "pehla": 0, "2": 1, "two": 1, "second": 1, "doosra": 1,
             "3": 2, "three": 2, "third": 2, "teen": 2, "teesra": 2}
CHOICE = re.compile(r"\b(" + "|".join(_ORDINALS) + r")\b", re.I)


def choice(text):
    """The 0-based choice the words name ("the second one" -> 1), or None."""
    m = CHOICE.search(text or "")
    return _ORDINALS[m.group(1).lower()] if m else None


def choices(text):
    """Every choice the words name ("one and three" -> [0, 2]), in order, no repeats."""
    return list(dict.fromkeys(_ORDINALS[m.group(1).lower()] for m in CHOICE.finditer(text or "")))


# Answers to a question card (app.asking): "skip" = use your best guess; "other: ..." = my own answer; "all of them".
SKIP = re.compile(r"^\W*(please\s+)?(skip( it| that| this( one)?)?|you (choose|decide|pick)|your (call|choice|guess)|"
                  r"(use )?your (best )?guess|whatever you think|(it )?doesn'?t matter|tum (decide|chuno) karo)\W*$", re.I)
OTHER_ANSWER = re.compile(r"^\W*(?:other|something else|none of those|neither)\W*[:,-]\s*(.{2,})$", re.I)
ALL_OF_THEM = re.compile(r"^\W*(all( of them| of those| three| four)?|every ?(one|thing)|sab|saare)\W*$", re.I)


# The manager (nova/actions/manager.py, plan 4a): requests that are a job for a worker, not a laptop task.
# Building something new ("make me a website", "create a landing page") - a determiner is needed, so "make the app
# full screen" stays a laptop task.
BUILD_THING = re.compile(
    r"\b(build|create|make|scaffold|develop|code)\s+(me\s+|us\s+)?(a|an|new|a new|my own)\b(?:\s+[\w'-]+){0,3}?\s+"
    r"(web ?site|site|web ?page|landing page|home ?page|web ?app|app|application|dashboard|api|cli tool|game|"
    r"browser extension|extension|bot|portfolio|blog)\b", re.I)
# A change in the user's own code: "fix the bug in my project", "update the README".
PROJECT_CODE = re.compile(
    r"\b(fix|change|update|refactor|add|remove|rename|clean up|improve)\b.{0,60}?\b(in|of|to|for)\s+(my|the|our|this)\s+"
    r"(project|repo|repository|codebase|code|web ?app|website)\b|\b(fix|update|edit|rewrite)\b.{0,30}?\breadme\b", re.I)
# Research first, then build with it: "research the best chart library, then add it to my project".
RESEARCH_THEN_BUILD = re.compile(
    r"\b(research|look up|find out|compare)\b.{3,120}?\b(then|and then|after that)\b.{0,40}?"
    r"\b(add|build|use|put|implement|make|create|code)\b", re.I)
WEBSITE = re.compile(r"\b(web ?site|site|web ?page|landing page|home ?page|portfolio|blog)\b", re.I)
README = re.compile(r"\breadme\b", re.I)               # a README job: its own type in the job history (4b)
BIG_JOB = re.compile(r"\b(new feature|multi-?file|whole|entire|from scratch|full)\b", re.I)
NEW_OR_EXISTING = re.compile(r"\b(new|from scratch|existing|my (current|existing)|in (my|the) (project|repo))\b", re.I)
STACK_NAMED = re.compile(r"\b(python|react|vue|angular|svelte|node(\.?js)?|typescript|javascript|html|flask|django|"
                         r"fastapi|next\.?js|rust|golang|java|c#|c\+\+|php|ruby)\b", re.I)


EMAIL = re.compile(r"\b(e-?mails?|mails?|gmail|inbox|unread|otp|verification code)\b", re.I)    # her mail stays local
# "ask Gemini to ...": in local-first mode the task starts on the cloud brain (core/app/brains.py)
ASK_CLOUD = re.compile(r"\b(ask|use|try|with)\s+(gemini|chatgpt|claude|the cloud|your cloud( brain)?|the cloud brain|"
                       r"cloud brain|a (bigger|smarter|stronger) brain)\b", re.I)


JOB_STATUS = re.compile(r"\b(how'?s|how is|is|status of|progress (of|on)|done with|finished)\b.{0,40}\b(job|jobs|"
                        r"worker|research)\b|\b(job|jobs|worker)\b.{0,30}\b(going|done|finished|ready|status|"
                        r"progress)\b", re.I)


WATCH_SEARCH = re.compile(r"\b(show me|on (my|the) screen|in front of me|open (google|chrome)|let me (see|watch)|"
                          r"browse|in chrome|in the browser)\b", re.I)


# --- Answers to her offers (from app/integrations.py) ---


SHOW = re.compile(r"^\W*(yes|yeah|yep|sure|ok(ay)?|haan?|show( it| me)?|open it|let me see|please)\b", re.I)


HELP_YES = re.compile(r"^\W*(help( me)?|close it|fix it|please)\b", re.I)


# --- Learning from the user (from app/learning.py) ---


ANSWER_YES = re.compile(r"^\W*(yes|yeah|yep|sure|ok(ay)?|do it|go ahead|please( do)?|save it|haan?|han|ji|"
                        r"of course)\b", re.I)


RESEARCH = re.compile(
    r"\b(research|look (it |that |this )?up|look into|find out|search|google|investigate|dig (in|into)|compare|"
    r"comparison|versus|vs\.?|latest|news|current|today'?s|prices?|reviews?|best|top \d+|sources?|report|"
    r"on the (web|internet)|online|in the background|deep dive)\b", re.I)


WATCH_START = re.compile(r"^\W*(ok(ay)?\W+|hey\W+)?(nova\W+)?(please\W+)?(watch me\b|watch (how|what) i do\b|"
                         r"watch and learn\b|let me show you\b|i'?ll show you( how)?\b|learn (this|from me)\b|"
                         r"record (this|what i do)\b)", re.I)


WATCH_DONE = re.compile(r"^\W*(ok(ay)?\W+|so\W+)?(nova\W+)?(i'?m |i am |all )?(done|finished|that'?s it|that'?s all|"
                        r"stop watching|you can stop( watching)?)(\W+nova)?\W*$", re.I)


# The WHOLE message is small talk - a greeting, thanks, "how are you", a joke, a compliment - with nothing to do on the
# laptop: she answers with no tools (a short, stable prompt the model can reuse: faster) and isn't nudged to "do" it.
# QA 2026-11-25: "hi" took 12 s, and its promise nudge turned "Hey Vikrant!" into "Oops! Sorry - I'm back on track".
# Not "ok" / "yes" (an answer to her question) and not "hi, open chrome" (a request).
_HELLO = (r"(hi+|hello+|hey+|hiya|namaste|namaskar|good (morning|afternoon|evening|night)|"
          r"how are you( doing)?( today)?|how('s| is) it going|how('re| are) things|what'?s up|wassup|sup|kaise ho|kya haal hai|"
          r"thanks?( (a lot|so much|you( so much| very much)?))?|thank you( (so|very) much)?|thx|ty|shukriya|dhanyavaa?d|"
          r"tell me (a|another|one more) (short |quick |funny |good )?joke( about [a-z ]{1,30})?|(another|one more) joke|"
          r"(you('re| are)|you're) (great|awesome|amazing|the best|so (sweet|cute|smart|funny))|good (job|girl|work)|"
          r"well done|nice one|love you|haha+|lol+|hehe+)")
SMALL_TALK = re.compile(r"^\W*((hi|hey|hello|ok(ay)?|oh|aww+|wow)\W+)?" + _HELLO + r"(\W+(nova|lucy|makima|buddy|dear|yaar))?"
                        r"(\W+" + _HELLO + r")?(\W+(nova|lucy|makima|buddy|dear|yaar))?\W*$", re.I)

# "try again" / "do it again": a follow-up on the task before, which needs its whole story (resume.fold_failed
# leaves that task's turns alone).
TRY_AGAIN = re.compile(r"^\W*((please|ok(ay)?|nova|so|yes|haan)\W+)*(try (it |that |this )?again|retry|"
                       r"do (it|that|this) again|one more (time|try|go)|phir se( (karo|try karo))?|dobara( karo)?)\b",
                       re.I)

CORRECTION = re.compile(
    r"(that'?s (wrong|not right|not what|incorrect)|not what i (asked|said|meant|wanted)|wrong (one|window|app|tab|file|thing)|"
    r"\byou (didn'?t|did not|forgot|missed|got (it|that) wrong|made a mistake|misunderstood|misheard)\b|"
    r"\bi (said|told you|asked you|asked for|meant)\b|^\W*no[,.! ]+(not|i |that|it|you|wrong)|why did you|stop doing|"
    r"don'?t do that|\bincorrect\b|\bgalat\b|\bnot like that\b)", re.I)


ABOUT_ME = re.compile(
    r"\b(my name is|call me|i am a|i'?m a|i work|i study|my job|i like|i love|i enjoy|i prefer|i hate|i don'?t like|"
    r"my favou?rite|i usually|i always|i never|every (day|morning|evening|night|week)|i live|i'?m from|i am from|"
    r"my (wife|husband|mom|mother|dad|father|brother|sister|friend|boss|girlfriend|boyfriend|son|daughter|team|company)|"
    r"remember that|from now on|next time|in future|always (do|use|open|reply|answer)|never (do|use|open))\b", re.I)


# --- How hard a question is (from app/prompts.py) ---


HARD = re.compile(
    r"\b(why|explain|compare|plan|analy[sz]e|reason|step[- ]by[- ]step|think (it|this) through|figure out|calculate|"
    r"solve|debug|code|script|program|research|draft|essay|summari[sz]e|pros and cons|best way|strategy|"
    r"difference between|optimi[sz]e|troubleshoot|math|proof|puzzle)\b|\bhow (do|does|can|should|would|to)\b", re.I)


# --- Asking her body to do something (from app/body.py) ---


_NAME = r"(?:hey |hi |ok |okay )?(?:nova|novah|noah|nover|nava|nowa|sophia)"


BODY_POLITE = re.compile(rf"^\W*(?:{_NAME}\W+)?(?:(?:no|nope|nah|arey|arre|yaar|oh)\W+)*|(?:\W+{_NAME})?\W*$", re.I)


BODY_ASK = re.compile(
    r"^(?:(?:can|could|will|would) you |please |now |just |go |ok |okay |come on |show me |let'?s see you |"
    r"i want (?:to see )?you to |i'?d like you to |do you know how to )*"
    r"(?:(?P<verb>do|give me|show me|perform|try) (?:a |an |the |some |your |my |that |one |another |me a )?"
    r"(?:little |quick |small |cute |nice |happy |big |proper |real |usual |normal |regular |own )?)?"
    r"(?P<what>[a-z][a-z' -]{1,30}?)"
    r"(?:\s+(?:for me|for us|again|once more|now|please|one more time|properly|for real|a bit|"
    r"(?P<times>two|three|four|five|\d) times|twice))*\W*$", re.I)


BODY_NOT_DONE = re.compile(
    r"\b(?:you'?re not|you are not|you aren'?t|you'?re still not|you are still not|you never)\s+(?P<a>\w+ing)\b|"
    r"\byou (?:didn'?t|did not|don'?t|do not|haven'?t)\s+(?P<b>dance|clap|spin|laugh|wave|bow|jump)\b|"
    r"\b(?:i )?(?:can'?t|cannot|don'?t) see you\s+(?P<c>\w+ing)\b", re.I)



DEFAULT_NAMES = "nova"
JUST_STOP = just_stop_re(DEFAULT_NAMES)       # voice_io builds its own from the character's wake names


KINDS = {
    "sleep": SLEEP, "stop": JUST_STOP, "yes": YES, "no": NO, "drop_card": DROP_CARD, "youtube_search": YOUTUBE_SEARCH,
    "camera": CAMERA, "screen": SCREEN, "selection": SELECTION, "read_more": READ_MORE,
    "continue": CONTINUE, "resume": RESUME, "skip": SKIP, "all_of_them": ALL_OF_THEM, "build_thing": BUILD_THING, "project_code": PROJECT_CODE,
    "research_then_build": RESEARCH_THEN_BUILD, "email": EMAIL, "ask_cloud": ASK_CLOUD, "job_status": JOB_STATUS, "watch_search": WATCH_SEARCH,
    "show": SHOW, "help_yes": HELP_YES,
    "answer_yes": ANSWER_YES, "research": RESEARCH, "watch_start": WATCH_START, "watch_done": WATCH_DONE,
    "correction": CORRECTION, "try_again": TRY_AGAIN, "small_talk": SMALL_TALK, "about_me": ABOUT_ME,
    "hard": HARD,
    "body_ask": BODY_ASK, "body_not_done": BODY_NOT_DONE,
}
TABLES = {"video": VIDEO, "fact": FACT}       # pattern -> what to do; the first that matches wins


def match(kind, text):
    """Do the user's words say `kind`? For a table kind ("video", "fact") this is what to do, or None."""
    text = text or ""
    if kind in TABLES:
        return next((what for rx, what in TABLES[kind] if rx.search(text)), None)
    if kind == "body_ask":
        return bool(BODY_ASK.match(BODY_POLITE.sub("", text.strip()).strip()))
    return bool(KINDS[kind].search(text))
