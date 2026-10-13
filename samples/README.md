# Sample briefs

Five research briefs across different industries and question types. [briefs.json](briefs.json) is the machine-readable source (the evaluation script in task 8.2 reads it); this page documents what a good report for each one looks like.

Every brief fits the 500-character input limit.

## Expected structure of every report

| Part | Expectation |
|---|---|
| Executive Summary | At most 150 words, grounded in cited evidence |
| Body | One section per research sub-task (3 to 6), each with a confidence label (HIGH / MEDIUM / LOW = lowest among the evidence it cites) |
| Citations | `[Source: domain, date, credibility: x.xx]` after each supported claim; no claim without one |
| Gaps & limitations | What could not be found or verified (for example non-public pricing) |
| Fact-check | Pass rate at or above 80% on the sampled claims; failures marked `[UNVERIFIED]` |

## The briefs

| ID | Industry | Question type | Uses PDFs |
|---|---|---|---|
| `payroll-sea` | SaaS / HR tech | Competitive landscape | No |
| `ai-imaging-eu` | Healthcare / medical devices | Market sizing and trends | No |
| `catl-profile` | Energy / EV batteries | Company deep-dive | No |
| `dpp-fashion` | Retail / fashion | Regulatory impact analysis | No |
| `bnpl-annual-reports` | Finance / consumer credit | Document-grounded comparison | Yes (2 to 3 annual reports, supplied by the evaluator) |

### `payroll-sea`: B2B payroll software in Southeast Asia

> Summarize the competitive landscape for B2B payroll software in Southeast Asia. Focus on pricing, recent funding, and product gaps. Cover the last 18 months and name the main regional and global vendors.

- **Expected sub-tasks:** main vendors and positioning; pricing models; recent funding and M&A; product gaps.
- **Must cover:** pricing, funding, Singapore and Indonesia, compliance, named competitors.
- **Watch for:** pricing is often not public. The report should say so under Gaps & limitations instead of guessing.

### `ai-imaging-eu`: AI diagnostic imaging in Europe

> How large is the European market for AI-assisted diagnostic imaging (radiology), and how fast is it growing? Cover adoption by hospitals, the CE marking and EU MDR regulatory burden, and the leading vendors. Prefer sources from the last 2 years.

- **Expected sub-tasks:** market size and growth; hospital adoption and reimbursement; regulatory path (CE marking, MDR, AI Act); leading vendors and deals.
- **Must cover:** market size, growth rate, MDR, reimbursement, vendor names.
- **Watch for:** analyst firms disagree on market size. The report should show both figures with their sources and flag the conflict, not average them.

### `catl-profile`: CATL company profile

> Profile CATL (Contemporary Amperex Technology): recent capacity expansion, major customers, margins and financial position, technology strategy (sodium-ion, LFP), and the main geopolitical and supply-chain risks. Use sources from the last 12 months.

- **Expected sub-tasks:** capacity expansion; customers and share; financial performance; technology roadmap; geopolitical and supply-chain risks.
- **Must cover:** capacity (GWh), market share, gross margin, sodium-ion, tariffs or export controls.
- **Watch for:** newer figures must outrank older ones for the same metric (recency ranking); entities, figures and dates should be extracted correctly.

### `dpp-fashion`: EU Digital Product Passport and fashion retail

> What does the EU Digital Product Passport (ESPR) mean for fashion and apparel retailers? Cover the timeline, the data retailers must provide, expected compliance costs, and how early movers are preparing. Flag anything still undecided.

- **Expected sub-tasks:** scope and timeline; data and traceability requirements; compliance cost estimates; retailer preparation.
- **Must cover:** ESPR, delegated acts, timeline, textiles, compliance cost.
- **Watch for:** rules that are not final must be labelled as proposals. A cost section built on few sources should be LOW confidence.

### `bnpl-annual-reports`: BNPL providers from annual reports

> Using the uploaded annual reports, compare the buy-now-pay-later providers on revenue growth, credit losses, and stated strategy. Add recent web context on regulation and funding costs, and say where the reports and the web disagree.

- **Expected sub-tasks:** revenue growth, credit losses and strategy (all from the reports); regulation and funding costs (web).
- **Must cover:** revenue growth, credit losses, provisions, regulation, provider names.
- **Watch for:** citations for report claims point at the uploaded documents and web claims at web domains; disagreements between the two are called out. The PDFs are not stored in this repository: the evaluator supplies them (annual reports of providers such as Klarna, Affirm or Afterpay/Block).

## Running a sample

Start the API and frontend, paste the brief into the home page (or `POST /research/run` with `brief=<text>`), and for `bnpl-annual-reports` attach the PDFs.
