from flask import Flask, render_template
from pathlib import Path
import markdown
import re

app = Flask(__name__)
OUTPUTS_DIR = Path("outputs")


def postprocess_html(html: str) -> str:
    return re.sub(
        r'\((<a\s[^>]+>[^<]*</a>(?:\s*/\s*<a\s[^>]+>[^<]*</a>)*)\)',
        r'<span class="sources">\1</span>',
        html,
    )


@app.route("/")
def index():
    files = sorted(OUTPUTS_DIR.glob("digest-*.md"), reverse=True)
    if not files:
        return "No digest available yet.", 404
    html = markdown.markdown(
        files[0].read_text(encoding="utf-8"),
        extensions=["tables"],
    )
    html = postprocess_html(html)
    return render_template("index.html", content=html)


if __name__ == "__main__":
    app.run(debug=True)
