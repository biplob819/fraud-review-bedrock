# ROI model

*Illustrative. This is a simulated scenario for a bank that does not exist. Inputs marked "simulated" come from the simulated evaluation in `docs/measurements/summary-eval2.json`. The rest are assumptions you should replace.*

`roi-model.xlsx` estimates what the fraud-review agent saves each month and how long it takes to pay back. `scripts/build_roi.py` builds it, and `src/fraudreview/roi.py` holds the same formulas in Python. A test checks that the two agree.

## How to use it

Open `roi-model.xlsx`. Yellow cells are inputs. Green cells are formulas. Change the yellow cells only.

| Input | Meaning | Default and source |
| --- | --- | --- |
| A | Alerts per month | 20,000 (illustrative, simulated scenario) |
| m | Minutes an analyst spends per alert | 4 minutes (assumption in the simulated run) |
| c | Cost per analyst hour | $40 (illustrative, simulated scenario) |
| r | Share of alerts the agent auto-clears | 8.8% (simulated eval, 44 of 500) |
| k | Share of freed hours that becomes a saving | 50% (illustrative, simulated scenario) |
| B | One-off baseline fixed cost (build and set-up) | $60,000 (illustrative, simulated scenario) |
| f | Fraud share of alerts | 11% (simulated eval, 55 of 500) |
| λ | Leakage rate: share of frauds the agent wrongly auto-clears | 5.3% (simulated eval, exact 95% upper bound for 0 of 55) |
| v | Average loss per fraud that slips through | $500 (illustrative, simulated scenario) |

The default λ is an upper bound, not an estimate. The run saw 0 leaks in 55 frauds, and the bound says the true rate could be as high as 5.3% (simulated). Using it gives a worst case.

## Formulas

- Hours saved per month = A × r × m / 60
- Gross saving per month = hours saved × c × k
- Leakage loss per month = λ × f × A × v
- Net saving per month = gross saving − leakage loss
- Payback in months = B / net saving. It reads "no payback" when net saving is zero or below.
- Break-even leakage rate = gross saving / (f × A × v). Above it, net saving turns negative.

## Worked example with the default inputs (illustrative, simulated)

- Hours saved: about 117 per month (simulated scenario).
- Gross saving: about $2,347 per month (illustrative, simulated scenario).
- Leakage loss at the 5.3% bound: about $58,312 per month (illustrative, simulated scenario).
- Net saving: about −$55,965 per month (illustrative, simulated scenario). Payback reads "no payback".
- Break-even leakage rate: about 0.21% (illustrative, simulated scenario).

The point is that the 0-of-55 result is too small a sample to rule out a loss this size. If you set λ to 0 (simulated scenario, no leakage at all), net saving is about $2,347 per month and payback is about 25.6 months. Treat that as a best case, not a forecast.

## What real data would change

Real data would replace every illustrative input. The bank's alert volume, handling time, analyst cost and average fraud loss would replace A, m, c and v. A large labelled sample of real alerts would replace r, f and λ, and a bigger sample would shrink the leakage bound a lot. Only then would the payback figure be worth quoting, and only after model risk review.
