import asyncio
from typing import Annotated
import os
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair
from mcp import ErrorData, McpError
from mcp.server.auth.provider import AccessToken
from mcp.types import TextContent, ImageContent, INVALID_PARAMS, INTERNAL_ERROR
from pydantic import BaseModel, Field, AnyUrl

import markdownify
import httpx
import readabilipy

# --- Load environment variables ---
load_dotenv()

TOKEN = os.environ.get("AUTH_TOKEN")
MY_NUMBER = os.environ.get("MY_NUMBER")

assert TOKEN is not None, "Please set AUTH_TOKEN in your .env file"
assert MY_NUMBER is not None, "Please set MY_NUMBER in your .env file"

# --- Auth Provider ---
class SimpleBearerAuthProvider(BearerAuthProvider):
    def __init__(self, token: str):
        k = RSAKeyPair.generate()
        super().__init__(public_key=k.public_key, jwks_uri=None, issuer=None, audience=None)
        self.token = token

    async def load_access_token(self, token: str) -> AccessToken | None:
        if token == self.token:
            return AccessToken(
                token=token,
                client_id="puch-client",
                scopes=["*"],
                expires_at=None,
            )
        return None

# --- Rich Tool Description model ---
class RichToolDescription(BaseModel):
    description: str
    use_when: str
    side_effects: str | None = None

# --- Fetch Utility Class ---
class Fetch:
    USER_AGENT = "Puch/1.0 (Autonomous)"

    @classmethod
    async def fetch_url(
        cls,
        url: str,
        user_agent: str,
        force_raw: bool = False,
    ) -> tuple[str, str]:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    url,
                    follow_redirects=True,
                    headers={"User-Agent": user_agent},
                    timeout=30,
                )
            except httpx.HTTPError as e:
                raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Failed to fetch {url}: {e!r}"))

            if response.status_code >= 400:
                raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Failed to fetch {url} - status code {response.status_code}"))

            page_raw = response.text

        content_type = response.headers.get("content-type", "")
        is_page_html = "text/html" in content_type

        if is_page_html and not force_raw:
            return cls.extract_content_from_html(page_raw), ""

        return (
            page_raw,
            f"Content type {content_type} cannot be simplified to markdown, but here is the raw content:\n",
        )

    @staticmethod
    def extract_content_from_html(html: str) -> str:
        """Extract and convert HTML content to Markdown format."""
        ret = readabilipy.simple_json.simple_json_from_html_string(html, use_readability=True)
        if not ret or not ret.get("content"):
            return "<error>Page failed to be simplified from HTML</error>"
        content = markdownify.markdownify(ret["content"], heading_style=markdownify.ATX)
        return content

    @staticmethod
    async def google_search_links(query: str, num_results: int = 5) -> list[str]:
        """
        Perform a scoped DuckDuckGo search and return a list of job posting URLs.
        (Using DuckDuckGo because Google blocks most programmatic scraping.)
        """
        ddg_url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
        links = []

        async with httpx.AsyncClient() as client:
            resp = await client.get(ddg_url, headers={"User-Agent": Fetch.USER_AGENT})
            if resp.status_code != 200:
                return ["<error>Failed to perform search.</error>"]

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", class_="result__a", href=True):
            href = a["href"]
            if "http" in href:
                links.append(href)
            if len(links) >= num_results:
                break

        return links or ["<error>No results found.</error>"]

# --- MCP Server Setup ---
mcp = FastMCP(
    "Job Finder MCP Server",
    auth=SimpleBearerAuthProvider(TOKEN),
)

# --- Tool: validate (required by Puch) ---
@mcp.tool
async def validate() -> str:
    return MY_NUMBER

# --- Tool: job_finder (now smart!) ---
JobFinderDescription = RichToolDescription(
    description="Smart job tool: analyze descriptions, fetch URLs, or search jobs based on free text.",
    use_when="Use this to evaluate job descriptions or search for jobs using freeform goals.",
    side_effects="Returns insights, fetched job descriptions, or relevant job links.",
)

@mcp.tool(description=JobFinderDescription.model_dump_json())
async def job_finder(
    user_goal: Annotated[str, Field(description="The user's goal (can be a description, intent, or freeform query)")],
    job_description: Annotated[str | None, Field(description="Full job description text, if available.")] = None,
    job_url: Annotated[AnyUrl | None, Field(description="A URL to fetch a job description from.")] = None,
    raw: Annotated[bool, Field(description="Return raw HTML content if True")] = False,
) -> str:
    """
    Handles multiple job discovery methods: direct description, URL fetch, or freeform search query.
    """
    if job_description:
        return (
            f"📝 **Job Description Analysis**\n\n"
            f"---\n{job_description.strip()}\n---\n\n"
            f"User Goal: **{user_goal}**\n\n"
            f"💡 Suggestions:\n- Tailor your resume.\n- Evaluate skill match.\n- Consider applying if relevant."
        )

    if job_url:
        content, _ = await Fetch.fetch_url(str(job_url), Fetch.USER_AGENT, force_raw=raw)
        return (
            f"🔗 **Fetched Job Posting from URL**: {job_url}\n\n"
            f"---\n{content.strip()}\n---\n\n"
            f"User Goal: **{user_goal}**"
        )

    if "look for" in user_goal.lower() or "find" in user_goal.lower():
        links = await Fetch.google_search_links(user_goal)
        return (
            f"🔍 **Search Results for**: _{user_goal}_\n\n" +
            "\n".join(f"- {link}" for link in links)
        )

    raise McpError(ErrorData(code=INVALID_PARAMS, message="Please provide either a job description, a job URL, or a search query in user_goal."))


# Image inputs and sending images

MAKE_IMG_BLACK_AND_WHITE_DESCRIPTION = RichToolDescription(
    description="Convert an image to black and white and save it.",
    use_when="Use this tool when the user provides an image URL and requests it to be converted to black and white.",
    side_effects="The image will be processed and saved in a black and white format.",
)

@mcp.tool(description=MAKE_IMG_BLACK_AND_WHITE_DESCRIPTION.model_dump_json())
async def make_img_black_and_white(
    puch_image_data: Annotated[str, Field(description="Base64-encoded image data to convert to black and white")] = None,
) -> list[TextContent | ImageContent]:
    import base64
    import io

    from PIL import Image

    try:
        image_bytes = base64.b64decode(puch_image_data)
        image = Image.open(io.BytesIO(image_bytes))

        bw_image = image.convert("L")

        buf = io.BytesIO()
        bw_image.save(buf, format="PNG")
        bw_bytes = buf.getvalue()
        bw_base64 = base64.b64encode(bw_bytes).decode("utf-8")

        return [ImageContent(type="image", mimeType="image/png", data=bw_base64)]
    except Exception as e:
        raise McpError(ErrorData(code=INTERNAL_ERROR, message=str(e)))
    
# --- Tool: reverse_text ---
ReverseTextDescription = RichToolDescription(
    description="Reverse the characters in a given string.",
    use_when="Use when the user asks to see their text backwards.",
    side_effects=None,
)

@mcp.tool(description=ReverseTextDescription.model_dump_json())
async def reverse_text(
    text: Annotated[str, Field(description="The text string to reverse")]
) -> list[TextContent]:
    """
    Returns the reversed version of the provided string.
    """
    reversed_str = text[::-1]
    return [TextContent(type="text", text=f"Reversed Text: {reversed_str}")]

EmojiReplacerDescription = RichToolDescription(
    description="Replace certain words in the text with fun emojis.",
    use_when="Use when the user wants to spice up their messages with emojis.",
    side_effects="Returns the transformed text with emojis inserted.",
)

@mcp.tool(description=EmojiReplacerDescription.model_dump_json())
async def emoji_replacer(
    text: Annotated[str, Field(description="Input text to replace words with emojis")]
) -> list[TextContent]:
    replacements = {
        "happy": "😊",
        "sad": "😢",
        "love": "❤️",
        "fire": "🔥",
        "cool": "😎",
        "cat": "🐱",
        "dog": "🐶",
        "party": "🎉",
    }

    words = text.split()
    replaced_words = [replacements.get(word.lower(), word) for word in words]
    replaced_text = " ".join(replaced_words)

    return [TextContent(type="text", text=replaced_text)]

from fastmcp import FastMCP
from mcp.types import TextContent
from typing import Annotated
from pydantic import Field

# Store state in memory for simplicity; for prod, use Redis or DB.
TRIVIA_QUESTIONS = [
    {"q": "What is the capital of France?", "options": ["A) Paris", "B) Berlin", "C) Madrid"], "answer": "A"},
    {"q": "Who wrote '1984'?", "options": ["A) Orwell", "B) Huxley", "C) Bradbury"], "answer": "A"},
    {"q": "What is 7 * 8?", "options": ["A) 54", "B) 56", "C) 58"], "answer": "B"},
]

# session_id -> {index, score}
trivia_sessions = {}

@mcp.tool(description="Play a trivia quiz game. Commands: 'trivia start' and 'trivia answer <A|B|C>'")
async def trivia(
    command: Annotated[str, Field(description="Trivia command from user")]
) -> list[TextContent]:
    global trivia_sessions

    import re

    # Parse session or create new id based on dummy (ideally from user ID)
    session_id = "default_session"  # For demo; extend to real sessions

    command = command.strip().lower()

    if command == "trivia start":
        trivia_sessions[session_id] = {"index": 0, "score": 0}
        q = TRIVIA_QUESTIONS[0]
        options_str = "\n".join(q["options"])
        return [TextContent(type="text", text=f"Trivia started! Question 1:\n{q['q']}\n{options_str}\nReply with 'trivia answer <option>'")]

    match = re.match(r"trivia answer ([abc])", command)
    if match:
        if session_id not in trivia_sessions:
            return [TextContent(type="text", text="No trivia session found. Please start with 'trivia start'.")]

        user_answer = match.group(1).upper()
        session = trivia_sessions[session_id]
        idx = session["index"]
        correct_answer = TRIVIA_QUESTIONS[idx]["answer"]

        if user_answer == correct_answer:
            session["score"] += 1
            result = "Correct! 🎉"
        else:
            result = f"Wrong! The correct answer was {correct_answer}."

        session["index"] += 1
        if session["index"] >= len(TRIVIA_QUESTIONS):
            final_score = session["score"]
            del trivia_sessions[session_id]
            return [TextContent(type="text", text=f"{result}\nGame Over! Your final score is {final_score}/{len(TRIVIA_QUESTIONS)}.")]

        next_q = TRIVIA_QUESTIONS[session["index"]]
        options_str = "\n".join(next_q["options"])
        return [TextContent(type="text", text=f"{result}\n\nNext Question {session['index'] + 1}:\n{next_q['q']}\n{options_str}\nReply with 'trivia answer <option>'")]

    return [TextContent(type="text", text="Invalid command. Use 'trivia start' or 'trivia answer <A|B|C>'.")]


# --- Run MCP Server ---
async def main():
    print("🚀 Starting MCP server on http://0.0.0.0:8086")
    await mcp.run_async("streamable-http", host="0.0.0.0", port=8086)

if __name__ == "__main__":
    asyncio.run(main())
