# Fraud-review policy v1

The triage agent may cite only the clauses below, by ID. Each clause has a plain-language rule and a machine-checked predicate over the alert's features. **Signal** clauses describe evidence of fraud. **Clear** clauses name the only conditions under which an alert may be cleared without a person. **No-clear** clauses forbid clearing whatever else applies. A signal on its own does not block a clear; the no-clear clauses are the hard limits, and the model weighs signals when it decides whether to cite a clear clause at all. The router checks every predicate in code: a clear is accepted only when a cited clear clause holds and no no-clear clause holds.

All thresholds are illustrative and set on simulated data; a bank would set its own.

## P3.1 Spend far above the card's norm
A purchase at five or more times the card's usual amount is a fraud signal.

```yaml
id: P3.1
kind: signal
fields: [amt_vs_usual]
when: amt_vs_usual >= 5
```

## P3.9 Never auto-clear extreme spend
A purchase at ten or more times the card's usual amount always goes to a person.

```yaml
id: P3.9
kind: no_clear
fields: [amt_vs_usual]
when: amt_vs_usual >= 10
```

## P4.2 First purchase at a merchant
The card has never been used at this merchant before.

```yaml
id: P4.2
kind: signal
fields: [merchant_first_seen]
when: merchant_first_seen
```

## P4.9 Never auto-clear a large first purchase at a merchant
A first purchase at a merchant of 200 or more always goes to a person.

```yaml
id: P4.9
kind: no_clear
fields: [merchant_first_seen, amount]
when: merchant_first_seen and amount >= 200
```

## P5.1 Purchase at night
A purchase between midnight and 5 a.m. local time is a weak fraud signal.

```yaml
id: P5.1
kind: signal
fields: [hour]
when: hour < 5
```

## P6.1 Rare category for this card
The card rarely buys in this category.

```yaml
id: P6.1
kind: signal
fields: [category_freq]
when: category_freq < 0.05
```

## P6.2 Thin card history
The card has fewer than five earlier transactions, so its usual pattern is uncertain.

```yaml
id: P6.2
kind: signal
fields: [card_history_n]
when: card_history_n < 5
```

## P5.9 Never auto-clear a purchase at night
A purchase from 10 p.m. to 6 a.m. local time always goes to a person. In this simulated data most fraud happens at night (see the data card).

```yaml
id: P5.9
kind: no_clear
fields: [hour]
when: hour >= 22 or hour < 6
```

## P6.9 Never auto-clear with almost no history
A card with fewer than three earlier transactions always goes to a person.

```yaml
id: P6.9
kind: no_clear
fields: [card_history_n]
when: card_history_n < 3
```

## P7.1 Routine purchase in a usual category
An alert may be cleared when the amount is in line with the card's history, the category is one the card uses often, and the merchant is not new.

```yaml
id: P7.1
kind: clear
fields: [amt_vs_usual, category_freq, merchant_first_seen]
when: amt_vs_usual < 2 and category_freq >= 0.2 and not merchant_first_seen
```

## P7.2 Small purchase at a known merchant
An alert may be cleared when the amount is under 50, the merchant is known to the card, and the card has a settled history.

```yaml
id: P7.2
kind: clear
fields: [amount, merchant_first_seen, card_history_n]
when: amount < 50 and not merchant_first_seen and card_history_n >= 10
```

## P7.3 Daytime purchase in the card's usual pattern
An alert may be cleared when it falls in the daytime, the amount is close to usual, and the category is familiar.

```yaml
id: P7.3
kind: clear
fields: [hour, amt_vs_usual, category_freq]
when: hour >= 8 and hour < 22 and amt_vs_usual < 1.5 and category_freq >= 0.1
```

## P8.9 Never auto-clear a very high risk score
An alert the baseline scores at 0.9 or above always goes to a person.

```yaml
id: P8.9
kind: no_clear
fields: [risk_score]
when: risk_score >= 0.9
```
