# Roadmap

Where the project is going. This is a living document — priorities shift as
we learn from actual usage. If you want to work on something here, open an
issue titled `roadmap: <topic>` before you start so we can align on scope.

## Now (in progress)

- **Corpus building for Abuja.** The bot only helps to the extent it knows
  the routes. Every submitted-and-approved corridor makes the answer set
  better. Contribution-side polish (better recording flow, alias merging,
  admin review UX) lives here.
- **Meta WhatsApp Business Cloud API (production channel).** Development
  runs on the Twilio sandbox. The Meta adapter already exists in
  [`app/channel/`](app/channel/); the remaining work is production
  verification with Meta and cutover.

## Next (planned)

- **Multi-language replies (main Nigerian languages).** Hausa, Yoruba, Igbo,
  and Pidgin — starting with reply templates and expanding to intent parsing.
  Language detection from the incoming message + a user preference stored on
  the session. English stays the default until a user opts in.
- **Voice input and voice replies.** Accept WhatsApp voice notes as an
  input modality (transcribe → route through the existing intent parser),
  and optionally reply with a short synthesised voice message when the
  user's last message was voice. Keeps the bot usable for commuters who
  don't type comfortably.

## Later (open questions before we commit)

- **Additional cities.** Corridor + anchor model is city-scoped, but we
  haven't stress-tested it beyond Abuja. Second-city launch is a research
  question: which city, who runs the contributor drive, do fares/modes
  differ enough to change the schema.
- **Saved locations / "home" and "work" shortcuts.**
- **Trip-planning across multiple corridors** (transfer between two known
  corridors when neither one covers the full trip on its own).
- **Rewards for contributors.** Some form of credit or recognition once
  we have real usage data to design around.

## Explicitly out of scope

- Driving directions (this is a commuter tool — bikes, taxis, keke,
  buses, walking).
- Business listings / a Yellow-Pages surface.
- A separate mobile app. WhatsApp is the interface.

---

Want to pick something up? File an issue, or comment on an existing one
tagged [`good first issue`](https://github.com/yusuf-abdoul/Whatsapp-Navigation-Assistant/labels/good%20first%20issue)
or [`help wanted`](https://github.com/yusuf-abdoul/Whatsapp-Navigation-Assistant/labels/help%20wanted).
