
## What a good summary is for this team, and why

A good summary has six qualities:

| Quality           | What it means                                                                                                             | Why it is important                                             |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| Risk first        | The first line gives the risk level and the main reasons.                                                                 | The reader decides from the first line.                         |
| All red flags     | The summary shows every red flag (a fact in the record that can be a risk, for example a court case against the company). | A missed risk costs more than some extra words.                 |
| Correct           | Every number comes from the record.                                                                                       | After one wrong number, the team would not trust the summaries. |
| Short             | 120 words or fewer                                                                                                        | The summary must save time.                                     |
| Same order        | Risk, Profile, Financials, Litigation, People                                                                             | The reader can compare companies quickly.                       |
| Honest about gaps | The summary tells the reader when data is not there or is old.                                                            | Data that is not there is also a warning sign.                  |

I read all 50 company records before I wrote the system. Five facts in the data changed my plan:

- 3 old companies have no accounts. An old company without accounts is a warning sign.
- 5 companies started in 2023 and have a loss. A loss is normal for a new company, so the summary must give the age.
- 4 companies have a court request to close them (a winding-up petition). Their status can still show "Active".
- For 16 companies, the latest accounts are more than 15 months old.
- One company has 5 court cases, but it started all 5 cases itself. The number of cases alone does not show risk.

## What you built

The system has two parts. First, the code reads each record, finds the red flags and sets the risk level (High concern, Review or No major flags). It does not use a model. Then the model writes the summary text. It gets only the facts from the code, not the full record.

I chose this design for three reasons:

- The model cannot change the risk level. The same record always gets the same level.
- The model gets only correct facts, so it has less opportunity to invent facts.
- The code gives the correct answer for each company, so I can measure each summary with code.

The code sets the risk level like this:

| Risk level     | When                           | Example                                                                |
| -------------- | ------------------------------ | ---------------------------------------------------------------------- |
| High concern   | One or more serious red flags  | The company is in the process to close, or a court decided against it. |
| Review         | One or more medium red flags   | A loss, no accounts, or a recent name change                           |
| No major flags | No serious or medium red flags | The record can still show context, for example old accounts.           |

The code uses some limits which I chose by myself:

| Red flag | Limit | Reason |
|---|---|---|
| Low quick ratio | Less than 1.0 | The company cannot pay its short-term debts from its fast assets. |
| Low interest cover | Less than 1.5 | The profit is almost not enough to pay the interest on loans. |
| Recent change | Name or director changed in the last 24 months | A recent change of owner or name needs a question. |
| Old accounts | More than 15 months old | A year of accounts plus some months for the audit. Older accounts do not show the company now. |
| Young company | Less than 3 years old | This is only context. It does not make the risk level higher. |

This is a real summary from my final version (v3):

> **Risk: Review** — Former name Sinar Industries Sdn. Bhd. (changed 2025-06-25).
> - **Profile:** Active private company, Retail, Kuala Lumpur, incorporated 1990. Retail of textiles and baju kurung.
> - **Financials (FY2024):** Revenue RM19.37m (+7% vs FY2023), net profit RM473k, equity RM12.32m, audit opinion Unqualified. Accounts 18 months old.
> - **Litigation:** 5 case(s): 0 as defendant (0 pending or adverse), 5 as plaintiff.
> - **People:** 3 director(s). Largest shareholder: Director 1 (90%). 1 of 3 directors appointed in the last 24 months.

All three model versions (v1, v2 and v3) used `gemini-3.5-flash-lite` from Google, on the free service.

I used the model on 10 of the 50 companies. The free service has a daily token limit, and with 10 companies I had enough tokens to compare more versions. I chose 10 companies with all three risk levels and the most difficult cases.

I made four versions:

| Version | What it is | Why I made it |
|---|---|---|
| v0 | A fixed template. No model. | A simple start point to compare with. It is always correct, but it shows only 3 reasons. |
| v1 | The model, with simple instructions | Can a model show all the red flags? |
| v2 | The model, with stricter instructions: an exact format, the most serious reasons first, and a maximum of 100 words | v1 was too long and did not put the most important reason first. |
| v3 | The v2 instructions, but the code writes every number in its short form (for example RM276k). The model only copies the numbers. | v2 had 1 wrong number and 5 amounts that were not in the short form, because the model made the short forms itself. |

I used Claude for these tasks:

- to review the company records and write the code
- to write a draft of this document

I directed the changes, ran every version, checked the outputs against the records, and reviewed all of the code before I submitted it.

## How you know whether it's good

I used two types of evidence: scores from a checker (my code that scores each summary without a model) and my own review of each summary.

### Scores

The checker answers four questions for each summary:

| Score | Question |
|---|---|
| Correct numbers | Does every number agree with the record? |
| Red flags shown | Does the summary show every red flag? |
| Serious flags shown | Does the summary show every serious and medium red flag? |
| Length | How many words does the summary have? |

Results for the 10 companies:

| Version | Correct numbers | Red flags shown | Serious flags shown | Mean words | Most words |
|---|---|---|---|---|---|
| v0 (template) | 100% | 56% | 77% | 64 | 85 |
| v1 (model) | 97.6% | 100% | 100% | 98 | 126 |
| v2 (model) | 99.0% | 100% | 100% | 76 | 109 |
| v3 (model) | **100%** | 100% | 100% | 78 | **108** |

In the checker output, these scores are `num_prec`, `flag_rec`, `hr_rec`, `words` and `max`. All versions also had 0 false alarms and the correct risk level.

The results show five things:

- The model versions show all red flags, but the template shows only 3 reasons. For one company in the process to close, the template does not show the court decision against the company.
- v2 is 22% shorter than v1. All v2 and v3 summaries have fewer than 120 words, but 2 v1 summaries have more than 120 words.
- All the wrong numbers have the same cause: the model cut the digits and did not round. v1 has 3, for example a quick ratio of 1.6174 as 1.61 (correct: 1.62). v2 has 1. v3 has none, because the code makes the short form of each number.
- v3 is the best version. All its numbers are correct and it shows all red flags. Its mean length is almost the same as v2 (78 words, v2 76), and its longest summary is shorter.
- The checker cannot find all problems. v1 also gives opinions that are not in the record. I found these opinions in my own review, not with the checker.

### My own review

The checker examines only numbers and keywords. It cannot tell if a summary helps the reader make the correct decision. So I read each of the 10 v3 summaries the way the team would: 30 seconds for each summary, before I looked at the record. For each one I asked: "Does this company need a closer look?" Then I compared my answer with the full record.

Result: I agree with 10 of 10 v3 decisions. Two summaries need a comment:

| Company | Decision | Comment |
|---|---|---|
| SYN-001158 | Correct, but weak | The company started in 2023 and has a loss of RM4.13m. The first line shows the loss, but not that the company is new. The age is in the Profile line, so a reader who decides from the first line can think that the loss is more serious than it is. A loss is normal for a new company. v1 put the age in the first line, but the v2 rule that keeps context out of the first line removed it. |
| SYN-000267 | Correct, but debatable | The only reason for Review is a name change 6 months ago. The summary follows the rule, but I am not sure that a name change alone needs a closer look. If there is a problem, it is in the rule. |

I did not do a separate review of v2. The code sets the risk level, so v2 and v3 give the same decision for each company. They differ only in the numbers and the format.

The checker also missed a problem in v1: it gives opinions that are not in the record, for example "stable financials with an unqualified audit opinion and active status" and "prevents full capability assessment". The stricter instructions in v2 and v3 stopped these opinions.

Problems in v2, and the result in v3:

| Problem in v2 | v3 |
|---|---|
| 1 number is wrong by a small amount. The record has a profit of RM944,841. The correct short form is RM945k, but v2 says RM944k. The model cut the digits and did not round. | Correct. v3 says RM945k. |
| 5 amounts in 3 summaries are not in the short form, for example RM10,491,800 and RM275,562. One amount has no "RM": "equity -18,522". | Correct. v3 says RM10.49m, RM276k and -RM19k. |

The change in v3 is small and follows the design: the code makes the short form of each number, and the model only copies it.

Apart from SYN-001158, v3 still has these problems:

- Some text has two negatives. "negative operating cash flow of -RM1.06m" (SYN-001158) comes from my code. "down -22%" (SYN-001212) comes from the model.
- The first line of SYN-000451 gives "Negative equity, net loss, negative operating cash flow" with no amounts. The reader must find the amounts in the Financials line.
- 2 summaries (SYN-000545 and SYN-001212) have more than the 100-word limit in the instructions (108 and 106 words). Both have fewer than 120 words.
- 2 summaries (SYN-000217 and SYN-000302) do not end with a full stop. SYN-000217 also ends with "No people flags", which does not help the reader. SYN-000381 gives the same shareholder two times.

## What you'd do next, and how you'd keep improving it

This work is not complete:

- I used the model on only 10 of the 50 companies.
- I chose the red flags and the limits. If a limit is wrong, the checker cannot find the problem.
- Only one person reviewed the summaries, and that person made the system.
- The team did not see the summaries.

Next steps:

1. Put the age of a new company in the first line.
2. Remove the two negatives from the code text, for example "Negative operating cash flow of -RM1.06m".
3. Add a final check in the code. It rejects a summary that is too long or has a number that is not in the record.
4. Use v3 on all 50 companies.
5. Run each version more than one time. The same instructions can give a slightly different summary each time, and more runs show if the scores stay the same.

To improve the system over time:

1. Show 10 summaries to the team. Ask which facts they need first and which limits are wrong.
2. Ask a team member to give a risk level to 30 companies. Compare the system with these answers.
3. Run the checker before each change. Do not release a version with more wrong numbers.
4. Compare each company with other companies in its industry, not with one fixed limit.
