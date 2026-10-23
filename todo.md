job queue
rate limiting
llm under the hood
web ui with dashboards
ban certain numbers
set range
database
explore redis

---

bias as a model dna
chain of prompts -> each removes the most given answer -> get unique array of answers (bias chain)

---

https://arxiv.org/pdf/2607.10252

- we know that there is some measurable fingerprint
- 4 languages
- questions like:
    > Task Answer space Condition
    > random number 1–100 closed (100) random
    > random number 1–10 closed (10) random
    > favorite number open (numeric) favorite
    > random letter closed (alphabet) random
    > random word open random
    > random color open (canonicalized) random
    > favorite color open (canonicalized) favorite
    > random animal open random
    > random city open random
    > coin flip closed (2) random
- reasoning off
- no paraphrasing test

---

reasoning influence on the fingerprint

---

some suggestions from claude sonnet 5.5:

- Quantization and coordination: Do quantized or provider-variant copies of one model still coordinate with the original? This ties the paper’s deployment anomalies to the collusion work.
- Cross-lingual focal points: The paper probes four languages. Does a model pick different focal points per language, and does that break same-model coordination?
- Human–LLM cultural alignment: The coordination paper reports failures on culturally nuanced salience. Which cultures does each model’s fingerprint track?

- Temperature curves: Fingerprint the entropy-versus-temperature curve instead of two points [Speculation].
- Tested: The evaluation is binary: “is this endpoint model X, yes or no?” It doesn’t estimate how much of the traffic is X versus something else.
