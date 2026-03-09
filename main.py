from __future__ import annotations

from sbornik_bot.bot import SbornikBot
from sbornik_bot.config import load_settings



def main() -> None:
    settings = load_settings()
    bot = SbornikBot(settings)
    bot.run(settings.token)


if __name__ == "__main__":
    main()
