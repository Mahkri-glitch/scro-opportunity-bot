# SCRO Opportunity Bot

A free Discord alert system for the Semiconductor Career Readiness Organization
at UCF. It scans public employer job feeds every day and prioritizes internships,
co-ops, apprenticeships, and new-graduate programs connected to semiconductor
manufacturing.

## What it prioritizes

- Process, process-integration, yield, product, and manufacturing engineering
- Semiconductor equipment, field service, metrology, and failure analysis
- Deposition, lithography, etch, CMP, implant, diffusion, and epitaxy
- Packaging, test, reliability, facilities, automation, and process control
- Relevant electrical, mechanical, materials, chemical, industrial, hardware,
  supply-chain, and technician opportunities
- Roles explicitly open to bachelor's and master's students receive an
  additional ranking boost; PhD/postdoctoral-only titles are excluded
- Florida opportunities receive an additional ranking boost

Only roles with an explicitly identifiable U.S. location are eligible. Country
names in either the title or location are checked, and blank, vague, or
country-unspecified remote locations are excluded.

The bot currently scans verified public Workday feeds for Applied Materials,
KLA, Intel, Micron, GlobalFoundries, Analog Devices, and NXP. The scanner also
contains reusable adapters for Greenhouse, Lever, and Ashby so more employers
can be added without changing Python code.

## Cost

This version does not call OpenAI or any other paid AI/search API. The Discord
webhook is free, and standard GitHub-hosted Actions runners are free for public
repositories.

## Repository secret

The workflow requires one Actions secret:

| Secret | Purpose |
|---|---|
| `DISCORD_WEBHOOK_URL` | Posts alerts to the private SCRO Discord channel |

Never place the webhook URL in a repository file, issue, log, or chat message.
The previously created `OPENAI_API_KEY` secret is not used by this version and
may be deleted.

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

The scheduled workflow runs daily at **9:00 AM America/New_York**.

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
  "search_terms": ["intern", "co-op", "early career"],
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

An invalid employer source does not stop the complete scan. The workflow logs a
warning and continues with the remaining sources. If every source fails, the
workflow stops without changing `seen_jobs.json`.

## Local development

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scanner.py --dry-run
```

To test Discord locally, set `DISCORD_WEBHOOK_URL` in your environment and run:

```bash
python scanner.py --send-test
```
