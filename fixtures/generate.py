#!/usr/bin/env python3
"""Generate the synthetic newsletter fixtures in fixtures/emails/*.eml (stdlib only, deterministic).

Sender display names reuse the newsletter names that the baseline logic keys on (ING-3: translate
list, category map, HTML-body list), because the routing depends on them. All addresses use the
reserved ``.example`` TLD and every body is synthetic text written for this experiment.

Coverage (see docs/infra.md for the full matrix):
  01 morning-brew        translate, category news, plain body, link/caption stripping, emoji subject
  02 the-news            no quotes in From, non-ASCII subject/body, -0300 date
  03 the-news (dup)      same sender/subject/date as 02 -> removed by DUP-1
  04 rico-matinal        category finance, HTML body (base64), UTC date that rolls back a day in GMT-3
  05 tech-brew           translate, HTML body (quoted-printable)
  06 lennys-newsletter   apostrophe in name, MarkdownV2 special characters, double quotes
  07 the-neuron          RFC 2047 encoded emoji display name, -0500 date crossing midnight
  08 (no display name)   bare address in From -> empty slug, default category tech
  09 the-pragmatic-eng.  translate, longer body

Usage: python3 fixtures/generate.py            # rewrites fixtures/emails/
"""

from __future__ import annotations

import base64
import quopri
from dataclasses import dataclass
from email.header import Header
from pathlib import Path

OUT = Path(__file__).resolve().parent / "emails"
TO = "newsletters@local.test"


@dataclass(frozen=True)
class Fixture:
    slug: str
    from_header: str
    subject: str
    date: str
    plain: str
    html: str
    html_cte: str = "quoted-printable"  # or "base64"


def encoded_word(text: str) -> str:
    """RFC 2047-encode only when needed (ASCII stays readable)."""
    return text if text.isascii() else Header(text, "utf-8").encode(maxlinelen=998)


def qp(text: str) -> str:
    return quopri.encodestring(text.encode("utf-8")).decode("ascii")


def b64(text: str) -> str:
    return base64.encodebytes(text.encode("utf-8")).decode("ascii")


def build(fx: Fixture) -> bytes:
    boundary = f"=_wfx_{fx.slug}"
    html_body = b64(fx.html) if fx.html_cte == "base64" else qp(fx.html)
    lines = [
        f"From: {fx.from_header}",
        f"To: {TO}",
        f"Subject: {encoded_word(fx.subject)}",
        f"Date: {fx.date}",
        f"Message-ID: <{fx.slug}@fixtures.wfx.local>",
        "MIME-Version: 1.0",
        f'Content-Type: multipart/alternative; boundary="{boundary}"',
        "",
        f"--{boundary}",
        'Content-Type: text/plain; charset="utf-8"',
        "Content-Transfer-Encoding: quoted-printable",
        "",
        qp(fx.plain).rstrip("\n"),
        f"--{boundary}",
        'Content-Type: text/html; charset="utf-8"',
        f"Content-Transfer-Encoding: {fx.html_cte}",
        "",
        html_body.rstrip("\n"),
        f"--{boundary}--",
        "",
    ]
    return "\r\n".join(line.replace("\n", "\r\n") for line in lines).encode("ascii")


def html_page(title: str, paragraphs: list[str]) -> str:
    body = "\n".join(f"    <p>{p}</p>" for p in paragraphs)
    return f'<!DOCTYPE html>\n<html>\n  <head><meta charset="utf-8"><title>{title}</title></head>\n  <body>\n{body}\n  </body>\n</html>\n'


THE_NEWS_PLAIN = """Bom dia! Aqui está o seu resumo de sexta-feira.

ECONOMIA
O Banco Central manteve a taxa básica de juros em 10,50% ao ano (https://thenews.example/a/1). A decisão foi unânime e veio em linha com o esperado pelo mercado.

POLÍTICA
O Congresso aprovou em segunda votação o novo marco legal das startups. O texto segue para sanção.

TECNOLOGIA
Uma empresa brasileira lançou um modelo de linguagem treinado em português — veja os detalhes (https://thenews.example/a/2).

Até amanhã!
"""

THE_NEWS_HTML = html_page(
    "The News",
    [
        "Bom dia! Aqui está o seu resumo de sexta-feira.",
        '<b>ECONOMIA</b> O Banco Central manteve a taxa básica de juros em 10,50% ao ano. <a href="https://thenews.example/a/1">Leia mais</a>',
        "<b>POLÍTICA</b> O Congresso aprovou em segunda votação o novo marco legal das startups.",
        "<b>TECNOLOGIA</b> Uma empresa brasileira lançou um modelo de linguagem treinado em português.",
    ],
)

PRAGMATIC_PLAIN = """Platform teams have changed a lot over the last two years. This issue looks at how.

Section 1. Team size. Most platform teams we surveyed have between five and twelve engineers. \
Smaller companies tend to fold platform work into infrastructure teams.

Section 2. Mandate. The most common mandate is developer productivity, followed by reliability and cost. \
Teams with a written charter reported fewer conflicts with product teams.

Section 3. Tooling. Internal developer portals are now common; golden paths for new services are the norm. \
Several teams moved their CI to ephemeral runners to cut queue times.

Section 4. Measuring impact. Lead time for changes and onboarding time were the two most cited metrics. \
Few teams measure developer satisfaction consistently.

Section 5. What is next. Respondents expect AI-assisted tooling to reshape the golden paths, \
and platform teams to own more of the model-serving stack (https://pragmaticengineer.example/p/1).

Thanks for reading. Reply with your own experience; I read every response.
"""

FIXTURES = [
    Fixture(
        slug="01-morning-brew",
        from_header='"Morning Brew" <crew@morningbrew.example>',
        subject="☕ Markets rally as rates hold steady",
        date="Sat, 14 Nov 2026 11:05:00 +0000",
        plain="""Good morning. Here is what you need to know today.

MARKETS
Stocks climbed for a third straight session (https://morningbrew.example/r/1) as the central bank held rates at 4.25%. Tech led the gains; energy lagged.

View image: https://img.morningbrew.example/chart.png
Caption: The index closed at a record high.

PRESENTED BY ACME CLOUD
Ship faster with Acme Cloud — try it free (https://acme.example/promo).

WORLD
A new trade agreement was signed between two regional blocs. Analysts expect lower tariffs on agricultural goods by 2027.

Follow image link: https://morningbrew.example/r/2
That's all for today — see you tomorrow!
""",
        html=html_page(
            "Morning Brew",
            [
                "Good morning. Here is what you need to know today.",
                '<b>MARKETS</b> Stocks climbed for a third straight session as the central bank held rates at 4.25%. <a href="https://morningbrew.example/r/1">More</a>',
                '<img src="https://img.morningbrew.example/chart.png" alt="Index chart">',
                "<b>PRESENTED BY ACME CLOUD</b> Ship faster with Acme Cloud.",
                "<b>WORLD</b> A new trade agreement was signed between two regional blocs.",
            ],
        ),
    ),
    Fixture(
        slug="02-the-news",
        from_header="The News <contato@thenews.example>",
        subject="Resumo do dia: economia, política e tecnologia",
        date="Fri, 13 Nov 2026 09:30:00 -0300",
        plain=THE_NEWS_PLAIN,
        html=THE_NEWS_HTML,
    ),
    Fixture(
        slug="03-the-news-duplicate",
        from_header="The News <contato@thenews.example>",
        subject="Resumo do dia: economia, política e tecnologia",
        date="Fri, 13 Nov 2026 09:30:00 -0300",
        plain=THE_NEWS_PLAIN,
        html=THE_NEWS_HTML,
    ),
    Fixture(
        slug="04-rico-matinal",
        from_header='"Rico Matinal" <matinal@rico.example>',
        subject="Rico Matinal: Ibovespa sobe 1,2% com exterior positivo",
        date="Sat, 14 Nov 2026 01:30:00 +0000",
        plain="Versão em texto indisponível. Abra a versão HTML (https://rico.example/matinal).\n",
        html=html_page(
            "Rico Matinal",
            [
                "<b>Bom dia, investidor!</b>",
                "O Ibovespa subiu 1,2% ontem, puxado por bancos e mineradoras. O dólar recuou para R$ 5,02.",
                'Agenda do dia: dados de inflação nos EUA às 10h30 (<a href="https://rico.example/agenda">agenda completa</a>).',
                "Destaque: a Petrobras anunciou dividendos extraordinários de R$ 0,85 por ação.",
                "<i>Este conteúdo não é recomendação de investimento.</i>",
            ],
        ),
        html_cte="base64",
    ),
    Fixture(
        slug="05-tech-brew",
        from_header='"Tech Brew" <crew@techbrew.example>',
        subject="Chipmakers bet big on edge AI",
        date="Thu, 12 Nov 2026 15:45:10 +0000",
        plain="Chipmakers bet big on edge AI. Read the full story online (https://techbrew.example/s/1).\n",
        html=html_page(
            "Tech Brew",
            [
                "<b>Chipmakers bet big on edge AI</b>",
                "Three of the largest chip designers announced low-power accelerators for phones and laptops (https://techbrew.example/s/1).",
                "Why it matters: running models on-device cuts latency and keeps data local.",
                '<img src="https://techbrew.example/i/chip.png" alt="A new accelerator">',
                "Also today: a robotics startup raised $40M; regulators opened a consultation on AI labels.",
            ],
        ),
    ),
    Fixture(
        slug="06-lennys-newsletter",
        from_header='"Lenny\'s Newsletter" <lenny@substack.example>',
        subject="How to run a pricing experiment [template] #42",
        date="Wed, 11 Nov 2026 13:00:00 +0000",
        plain="""Hi friends! This week: a step-by-step pricing experiment (with a [template]).

1. Pick one metric = revenue per visitor; ignore vanity stats.
2. Split traffic 50/50 -> control vs. "variant" (no peeking!).
3. Use a {holdout} group + track churn for 30 days.
* Tip: keep the _test_ small; *ship* the winner.
> "Price is a feature," says one founder. Costs: ~$0 | time: 2 weeks.

Code sample: `if variant == "B": price *= 1.1`
Questions? Reply to this email (https://substack.example/lenny/reply).
""",
        html=html_page(
            "Lenny's Newsletter",
            [
                "Hi friends! This week: a step-by-step pricing experiment.",
                "Pick one metric; split traffic 50/50; track churn for 30 days.",
            ],
        ),
    ),
    Fixture(
        slug="07-the-neuron",
        from_header=f"{Header('🧠 The Neuron', 'utf-8').encode()} <hi@theneuron.example>",
        subject="AI agents get a memory upgrade",
        date="Tue, 10 Nov 2026 23:59:59 -0500",
        plain="""Welcome back, humans. 🤖

Today: AI agents get long-term memory. A new open-source library lets assistants remember user preferences across sessions (https://theneuron.example/n/1).

Treats to try:
- A note-taking app that summarizes meetings in real time.
- A browser extension that explains any chart.

Caption: Our favorite robot of the week.
See you tomorrow!
""",
        html=html_page(
            "The Neuron",
            ["Welcome back, humans.", "Today: AI agents get long-term memory."],
        ),
    ),
    Fixture(
        slug="08-no-display-name",
        from_header="digest@weekly.example",
        subject="Weekly digest #17",
        date="Mon, 09 Nov 2026 08:00:00 +0000",
        plain="""Your weekly digest.

Top story: a city council approved a new bike-lane network. Second story: the local library extended weekend hours.
""",
        html=html_page("Weekly digest", ["Your weekly digest.", "Top story: new bike lanes."]),
    ),
    Fixture(
        slug="09-pragmatic-engineer",
        from_header='"The Pragmatic Engineer" <pragmaticengineer@substack.example>',
        subject="The state of platform teams in 2026",
        date="Sun, 08 Nov 2026 16:20:00 +0000",
        plain=PRAGMATIC_PLAIN,
        html=html_page(
            "The Pragmatic Engineer",
            ["Platform teams have changed a lot over the last two years."],
        ),
    ),
]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.eml"):
        old.unlink()
    for fx in FIXTURES:
        (OUT / f"{fx.slug}.eml").write_bytes(build(fx))
    print(f"wrote {len(FIXTURES)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
