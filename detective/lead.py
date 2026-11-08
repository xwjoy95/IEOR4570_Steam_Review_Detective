"""The Lead: clarifies the question, splits it into tasks, sends researchers in parallel, and checks
for gaps. Clarifying is its own agent with a structured output, so the code can stop early."""

from agents import Agent
from pydantic import BaseModel

from detective.llm import SETTINGS, SMART
from detective.researchers import research
from tools import get_game_info, get_review_trend, search_game

# --- Clarify ---


class Game(BaseModel):
    appid: int
    name: str


class Clarification(BaseModel):
    vague: bool
    ask: str = ""  # the question back to the user, when vague
    games: list[Game] = []  # one game, or two for a comparison
    focus: str = ""  # what to investigate, restated as one specific research question
    window: list[str] = []  # ["YYYY-MM-DD", "YYYY-MM-DD"], or empty for "recent"


CLARIFIER_PROMPT = """\
You decide whether a question about Steam games is specific enough to research. You don't research it.

The conversation so far may come before the question. Use it: a follow-up like "what about the
Chinese players?" or "what about veterans?" is about the same game as the previous question, and is not vague.

1. Find the game(s) with search_game. If one result's name matches the user's game exactly (or
   it's clearly the one they mean), use it. Ignore DLC and soundtracks.
2. Set vague=true and write `ask` when:
   - no specific game is named or implied by the conversation;
   - several different games match and you can't tell which one: list the candidates with their
     release years and ask which one;
   - no game matches: say so and ask for the exact Steam name;
   - the question has no direction ("how is it", "is it good", "fun or not"): ask what they want to know
     (why its reviews changed, a specific update, Chinese vs English players, veterans vs newcomers,
     whether it's worth buying now) and over what time range.
   Keep `ask` short, friendly, in the user's language.
3. Otherwise set vague=false, fill games, restate the question as one specific research question in
   `focus`, and set `window` if the user named a time range. You're told today's date: turn relative
   ranges into dates ("recently" / "the last few months" = the last 3 months up to today;
   "this year" = since January 1).

This tool covers Steam only: no console versions, no exact sales figures. Those questions are still
not vague; the report will say what the data can't answer."""

clarifier = Agent(
    name="Clarifier",
    instructions=CLARIFIER_PROMPT,
    model=SMART,
    model_settings=SETTINGS,
    tools=[search_game],
    output_type=Clarification,
)

# --- Plan, fan out, check for gaps ---

LEAD_PROMPT = """\
You lead a research team that explains why a Steam game's player sentiment changed. You're given
the question, the game (with its appid) and the focus.

Your researchers, each with its own tools and a fresh context:
- "reviews": Steam reviews. Weekly trend and its spikes, samples of reviews from a time window,
  splits by language (english / schinese / tchinese) or by playtime.
- "patches": the developer's announcements and patch notes by date, and store details (price,
  release date, genre).
- "players": monthly average and peak players (SteamCharts), current players, SteamSpy estimates.

1. Call get_review_trend first, to see when sentiment changed. Look at its spikes. You're told
   today's date and maybe a window: stay inside it. For "recently" with no window, look at the last
   3 months up to today, and mention an older spike only as one line of background.
2. Split the question into 2 to 4 independent tasks, one researcher each. Call research once per
   task, all in the same turn, so the researchers work in parallel. Each task names the game and
   appid, a concrete date window (from the trend), and exactly what to find out. Fit the tasks to
   the question and to the trend; don't assume something went wrong:
   - "Why did reviews drop / what went wrong": what reviewers criticised in the spike window; which
     updates landed just before and during it; whether player counts moved afterwards.
   - "How is it now / compared with launch / is it worth it": the positive/negative split and what
     players praise and criticise in each period; updates between them; player counts.
   - Comparisons (languages, playtime, two games): one task per side, each asking for both praise
     and criticism.
   Ask the reviews researcher for both sides unless the user asked about only one.
3. Read the findings. If part of the question still has no evidence, or findings contradict each
   other, call research again for just that part. Do this at most twice.
4. When the question is covered, or no researchers are left, reply DONE and nothing else: a writer
   writes the report from the findings."""

lead = Agent(
    name="Lead",
    instructions=LEAD_PROMPT,
    model=SMART,
    model_settings=SETTINGS,
    tools=[get_review_trend, get_game_info, research],
)
