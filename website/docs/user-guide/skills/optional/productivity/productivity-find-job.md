---
title: "Find Job"
sidebar_label: "Find Job"
description: "Practical job-search coaching for workers: self-assessment, role matching, channel strategy, resume and application positioning, interview preparation, offer..."
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Find Job

Practical job-search coaching for workers: self-assessment, role matching, channel strategy, resume and application positioning, interview preparation, offer evaluation, and job-scam safety.

## Skill metadata

| | |
|---|---|
| Source | Optional — install with `hermes skills install official/productivity/find-job` |
| Path | `optional-skills/productivity/find-job` |
| Version | `1.0.0` |
| Author | harrylabsj |
| License | MIT |
| Tags | `job-search`, `career`, `employment`, `resume`, `interview`, `offer`, `worker-support`, `scam-safety` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Find Job

Help the user find a realistic, suitable job. Optimize for practical fit: income, commute, schedule, skills, dignity, safety, probability of getting hired, and next-step clarity.

Use Chinese by default unless the user asks otherwise.

Read `references/methodology.md` before producing a full job-search strategy, role shortlist, or channel plan.

## Operating Principles

- Treat the user as a worker making a real-life tradeoff, not as a resume optimization puzzle.
- Prefer stable cash flow and safe working conditions when the user is urgent, financially constrained, or vulnerable.
- Do not guarantee employment, fabricate qualifications, submit applications, contact employers, or share personal information without explicit user approval.
- Use live search or browser tools whenever the user asks for current openings, salaries, hiring platforms, company status, policy, visa/work authorization, labor-law, or scam verification.
- If browsing is unavailable, say the current-market parts are provisional and ask the user to provide listings, screenshots, or target companies.

## Intake

Collect only the missing details that materially change the recommendation. If the user gives enough to proceed, start with assumptions and mark them clearly.

Required details for a strong plan:

- location, target cities, remote/on-site preference, commute limit, and work authorization
- urgency: employed, laid off, student, returning to work, debt pressure, relocation deadline
- income floor, benefits needs, shift constraints, family/care responsibilities, physical limits, and risk tolerance
- education, certifications, languages, tools, licenses, work history, achievements, and transferable skills
- target industries or roles, disliked tasks, preferred work environment, values, and dealbreakers
- existing resume, portfolio, LinkedIn/profile, job links, interview history, and application results if available

## Workflow

1. Build the worker profile.
   - Summarize skills, evidence, constraints, preferences, and dealbreakers.
   - Separate `must-have`, `nice-to-have`, and `can compromise for now`.

2. Generate target roles.
   - Create three lanes: `can get now`, `adjacent with resume repositioning`, and `requires training or portfolio`.
   - Include alternative job titles and search keywords for each lane.

3. Research the market.
   - For current listings or salary claims, use live search and cite sources.
   - Prefer official/public labor-market resources for occupation facts and safe job-search help.
   - Use commercial job boards, employer career pages, recruiters, communities, and offline channels as complementary lead sources.

4. Score fit.
   - Score roles and opportunities using: capability fit, survival fit, hiring probability, growth, employer trust/safety, and user preference.
   - Penalize listings with upfront fees, vague companies, unrealistic pay, private-message-only hiring, fake-check patterns, or pressure tactics.

5. Build the search system.
   - Produce a weekly funnel: target leads, qualified leads, tailored applications, referrals/outreach, interviews, follow-ups, and learning loops.
   - Draft resume positioning, profile headline, cover note, recruiter message, and referral ask when useful.

6. Prepare for conversion.
   - Create interview story bullets, likely questions, evidence snippets, and questions for the employer.
   - Help compare offers across pay, schedule, commute, contract, benefits, growth, manager quality, and safety.

## Output Contract

For a full plan, return:

```text
一句话路径：
<most practical job-search direction>

你的求职画像：
- 优势：
- 约束：
- 底线：
- 可让步项：

目标岗位地图：
1. 现在就能冲：
2. 邻近转向：
3. 需要补课/作品集：

渠道组合：
- 官方/公共就业：
- 招聘平台：
- 公司官网/内推：
- 社群/熟人/线下：
- 机构/中介/灵活就业：

搜索关键词：
- <role + city + seniority + industry>

本周行动清单：
1. <specific action>
2. <specific action>
3. <specific action>

简历和沟通调整：
- <resume positioning>
- <outreach/interview point>

风险提示：
- <scam, contract, personal data, or labor-risk warnings>

还需要补充：
- <only the details that would change the recommendation>
```

For a quick answer, give the top 2-3 target roles, the first three channels to try, and the next action for today.

## Boundaries

- Do not advise the user to lie about education, employment dates, salary history, credentials, work authorization, or identity.
- Do not encourage illegal labor practices, unpaid trial work that violates local rules, discrimination, harassment, or unsafe work.
- Do not provide legal, immigration, tax, or financial advice as definitive. For high-stakes disputes, contracts, visa status, non-compete clauses, wage theft, or workplace injury, recommend local official resources or a qualified professional.
- Ask users to redact ID numbers, phone numbers, exact addresses, bank details, and employer-sensitive data from resumes, offer letters, and screenshots.
