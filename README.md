# SCRO Opportunity Bot

An AI-assisted Discord alert system for the Semiconductor Career Readiness
Organization at UCF. Jensen Huang combines official employer job feeds with
Gemini grounded search, independently reviews the full duties of each opening,
and posts high-confidence semiconductor internships and co-ops every day.

## How opportunities are found and checked

- Official ATS collectors scan all 36 configured employers.
- A separate Gemini discovery agent uses grounded Google Search and URL Context
  to locate relevant live openings that an employer feed may have missed.
- The discovery agent may only return verified individual job pages on the
  configured official company or ATS host; aggregator and invented URLs are
  discarded in code.
- The review agent reads the full available description, so a generic title
  such as **Engineering Intern** can qualify when its actual duties clearly
  involve process, yield, manufacturing, product engineering, equipment,
  metrology, integration, lithography, etch, deposition, CVD, PVD, ALD, CMP,
  packaging, test, reliability, or semiconductor fabrication.
- The title must still identify an **internship** or **co-op**.
- Product-management roles are rejected; “product” qualifies only in an
  engineering, development, quality, reliability, or test context
- Roles explicitly open to bachelor's and master's students receive an
  additional ranking boost; PhD/postdoctoral-only titles are excluded
- Florida opportunities receive an additional ranking boost

Only roles with an explicitly identifiable U.S. location are eligible. Country
names in either the title or location are checked, and blank, vague, or
country-unspecified remote locations are excluded.

The bot now monitors all 36 requested companies through their official public
career systems:

- Equipment and metrology: Applied Materials, Lam Research, KLA, ASML, ASM
  International, Tokyo Electron, Onto Innovation, Axcelis Technologies, Veeco,
  MKS Instruments, and INFICON
- Chip manufacturing: Intel, Micron, GlobalFoundries, TSMC, Samsung
  Semiconductor, Texas Instruments, Wolfspeed, SkyWater Technology, onsemi,
  Analog Devices, Infineon, STMicroelectronics, NXP, and Qorvo
- Packaging and test: Amkor, ASE, Teradyne, and Advantest
- Materials and gases: Entegris, Air Products, Air Liquide, Linde, FUJIFILM
  Electronic Materials, Shin-Etsu, and GlobalWafers

The ATS layer supports Workday, Greenhouse, Lever, Ashby, both current Eightfold
career-site APIs, Oracle Recruiting, SuccessFactors, CSRF-protected Dayforce,
iCIMS, ADP Workforce Now, ADP MyJobs, Cornerstone, and official career pages.
For an official page that blocks GitHub's runner, its configured read-only
reader URL is used as a fallback.

## Cost

This version does not call OpenAI. It uses the Gemini API; selected Gemini
models currently have a limited free tier. The Discord webhook is free, and
standard GitHub-hosted Actions runners are free for public repositories. Google
may change model availability or free-tier quotas, so check its current pricing
before relying on it indefinitely.

## Repository secret

The workflow requires two Actions secrets:

| Secret | Purpose |
|---|---|
| `DISCORD_WEBHOOK_URL` | Posts alerts to the private SCRO Discord channel |
| `GEMINI_API_KEY` | Runs grounded discovery and semantic job review |

Never place either secret in a repository file, issue, log, or chat message.
An `OPENAI_API_KEY` secret is not used by this version and may be deleted.

Create a Gemini key in Google AI Studio, then open **Settings → Secrets and
variables → Actions → New repository secret** and name it exactly
`GEMINI_API_KEY`. If the key is missing, the workflow fails visibly instead of
silently reverting to the older keyword-only behavior.

## Test the Discord connection

1. Open **Actions** in this repository.
2. Select **Daily semiconductor opportunity scan**.
3. Choose **Run workflow**.
4. Leave the mode as **test-webhook**.
5. Select **Run workflow**.

The `#opportunity-alerts` channel should receive one confirmation message. This
test does not scan job boards or change duplicate-suppression state.

## Preview results safely

Run the workflow manually with **dry-run**. Matches appear in the workflow log,
but nothing is posted to Discord and `seen_jobs.json` is not changed.

## Run a real scan

Run the workflow manually with **scan-and-post**. The highest-ranked new matches
are posted to Discord, and all current matching job IDs are recorded in
`seen_jobs.json`. Subsequent runs only post opportunities that have not already
been seen.

The scheduled workflow runs at **13:17 UTC**, which is **9:17 AM Eastern
during daylight-saving time**. When no new roles are found, Jensen Huang sends
a compact completion message instead of leaving the Discord channel silent.

## Add another employer

Edit `companies.json` and add an entry matching the employer's applicant
tracking system.

### Workday

```json
{
  "company": "Example Semiconductor",
  "enabled": true,
  "host": "example.wd1.myworkdayjobs.com",
  "tenant": "example",
  "site": "External",
  "type": "workday",
  "search_terms": ["intern", "co-op"],
  "max_results_per_term": 80
}
```

### Greenhouse

```json
{
  "company": "Example Semiconductor",
  "enabled": true,
  "type": "greenhouse",
  "board_token": "example"
}
```

### Lever

```json
{
  "company": "Example Semiconductor",
  "enabled": true,
  "type": "lever",
  "site": "example"
}
```

### Ashby

```json
{
  "company": "Example Semiconductor",
  "enabled": true,
  "type": "ashby",
  "board_name": "example"
}
```

The other source formats used by the 36-company list are already represented
in `companies.json`; copy the closest entry when adding another employer that
uses the same applicant-tracking system.

An invalid employer source does not stop the complete scan. The workflow logs a
warning and continues with the remaining sources. If every source fails, the
workflow stops without changing `seen_jobs.json`.

## Local development

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scanner.py --dry-run
```

For a local scan, set `GEMINI_API_KEY`. To test Discord locally, also set
`DISCORD_WEBHOOK_URL` and run:

```bash
python scanner.py --send-test
```
