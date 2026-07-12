/*
 * options_math.js — pure options math for the "Options Trade Assessor".
 *
 * ZERO DOM, zero dependencies. Every function is pure: plain numbers in, plain
 * numbers/objects out. Loadable two ways, matching this repo's no-build style:
 *
 *   Browser:  <script src="scripts/options_math.js"></script>  -> window.OptionsMath
 *   Node:     const M = require("./scripts/options_math.js");   -> module.exports
 *             node scripts/options_math.js                      -> runs self-test
 *
 * Conventions (Nifty weekly index options, personal tool):
 *   - IV is passed as a PERCENT number, e.g. 12.5 means 12.5% annualised vol.
 *   - tYears is time to expiry in YEARS (days / 365).
 *   - type is 'CE' (call) or 'PE' (put).
 *   - r is the risk-free rate as a fraction (default 0.065 = 6.5%). No dividends
 *     (cash index), so this is plain Black-Scholes, not Black-76 / Merton.
 *   - theta is returned PER CALENDAR DAY (annual theta / 365), sign included
 *     (negative for long options).
 *   - vega is returned PER 1 PERCENTAGE-POINT of IV (annual vega / 100), so it
 *     lines up with IV being quoted in percent.
 */
(function (root, factory) {
    "use strict";
    var api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    // Always attach to the global when one exists (browser: window) so the same
    // file works via <script src> and via require() in the same repo.
    if (typeof window !== "undefined") { window.OptionsMath = api; }
    else if (typeof globalThis !== "undefined") { globalThis.OptionsMath = api; }
    return api;
})(this, function () {
    "use strict";

    var SQRT2PI = Math.sqrt(2 * Math.PI);

    // Standard normal PDF.
    function normPdf(x) { return Math.exp(-0.5 * x * x) / SQRT2PI; }

    // Standard normal CDF — Abramowitz & Stegun 7.1.26, |error| < 7.5e-8.
    function normCdf(x) {
        if (!isFinite(x)) return x > 0 ? 1 : 0;
        var t = 1 / (1 + 0.2316419 * Math.abs(x));
        var d = normPdf(x);
        var p = d * t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 +
                t * (-1.821255978 + t * 1.330274429))));
        return x >= 0 ? 1 - p : p;
    }

    // Guard against degenerate inputs (0 IV or 0 time) that would divide by zero.
    function _sigT(iv, tYears) {
        var sigma = (iv || 0) / 100;
        var sqrtT = Math.sqrt(Math.max(tYears, 0));
        return { sigma: sigma, sqrtT: sqrtT, denom: sigma * sqrtT };
    }

    function _d1d2(spot, strike, iv, tYears, r) {
        var s = _sigT(iv, tYears);
        if (s.denom <= 1e-12 || spot <= 0 || strike <= 0) {
            // At/after expiry (or zero vol): d1/d2 collapse to ±inf by moneyness.
            var m = Math.log((spot || 1e-9) / (strike || 1e-9));
            var big = m >= 0 ? 40 : -40;
            return { d1: big, d2: big, sigma: s.sigma, sqrtT: s.sqrtT };
        }
        var d1 = (Math.log(spot / strike) + (r + 0.5 * s.sigma * s.sigma) * tYears) / s.denom;
        var d2 = d1 - s.denom;
        return { d1: d1, d2: d2, sigma: s.sigma, sqrtT: s.sqrtT };
    }

    // Black-Scholes fair value (per unit of underlying, i.e. per index point).
    function bsPrice(args) {
        var spot = args.spot, strike = args.strike, tYears = args.tYears;
        var type = (args.type || "CE").toUpperCase();
        var r = args.r == null ? 0.065 : args.r;
        var x = _d1d2(spot, strike, args.iv, tYears, r);
        var disc = Math.exp(-r * Math.max(tYears, 0));
        if (type === "PE") {
            return Math.max(strike * disc * normCdf(-x.d2) - spot * normCdf(-x.d1), 0);
        }
        return Math.max(spot * normCdf(x.d1) - strike * disc * normCdf(x.d2), 0);
    }

    // Greeks. delta (per 1 point of spot), gamma (per 1 point), theta (per
    // CALENDAR DAY), vega (per 1 IV percentage-point).
    function bsGreeks(args) {
        var spot = args.spot, strike = args.strike, tYears = args.tYears;
        var type = (args.type || "CE").toUpperCase();
        var r = args.r == null ? 0.065 : args.r;
        var x = _d1d2(spot, strike, args.iv, tYears, r);
        var disc = Math.exp(-r * Math.max(tYears, 0));
        var pdf = normPdf(x.d1);
        var sqrtT = x.sqrtT || 1e-9;
        var gamma = pdf / (spot * x.sigma * sqrtT || 1e-9);
        var vegaAnnual = spot * pdf * sqrtT;               // per 1.00 (=100%) of sigma
        var term1 = -(spot * pdf * x.sigma) / (2 * sqrtT); // shared theta term (annual)
        var delta, thetaAnnual;
        if (type === "PE") {
            delta = normCdf(x.d1) - 1;
            thetaAnnual = term1 + r * strike * disc * normCdf(-x.d2);
        } else {
            delta = normCdf(x.d1);
            thetaAnnual = term1 - r * strike * disc * normCdf(x.d2);
        }
        return {
            delta: delta,
            gamma: gamma,
            theta: thetaAnnual / 365,   // per calendar day
            vega: vegaAnnual / 100      // per 1 IV percentage-point
        };
    }

    // Risk-neutral probability the option expires in-the-money = N(d2) for a call,
    // N(-d2) for a put.
    function probITM(args) {
        var r = args.r == null ? 0.065 : args.r;
        var type = (args.type || "CE").toUpperCase();
        var x = _d1d2(args.spot, args.strike, args.iv, args.tYears, r);
        return type === "PE" ? normCdf(-x.d2) : normCdf(x.d2);
    }

    // Risk-neutral probability the underlying TOUCHES `barrier` at any point before
    // expiry (one-touch), via the reflection principle on GBM log-returns.
    // Works for both an up barrier (barrier > spot) and a down barrier (< spot).
    function probTouch(args) {
        var spot = args.spot, barrier = args.barrier, tYears = args.tYears;
        var r = args.r == null ? 0.065 : args.r;
        var s = _sigT(args.iv, tYears);
        if (spot <= 0 || barrier <= 0) return 0;
        if (s.denom <= 1e-12) return 0;                     // no time / no vol -> can't travel
        var a = Math.log(barrier / spot);                   // log-distance to barrier
        var nu = r - 0.5 * s.sigma * s.sigma;               // risk-neutral drift of log-price
        var vT = s.denom;                                   // sigma * sqrt(T)
        var expo = Math.exp(2 * nu * a / (s.sigma * s.sigma));
        var p;
        if (a > 0) {         // up barrier: P(max log-return >= a)
            p = normCdf((nu * tYears - a) / vT) + expo * normCdf((-nu * tYears - a) / vT);
        } else if (a < 0) {  // down barrier: P(min log-return <= a)
            p = normCdf((a - nu * tYears) / vT) + expo * normCdf((a + nu * tYears) / vT);
        } else {
            p = 1;           // already at the barrier
        }
        return Math.min(Math.max(p, 0), 1);
    }

    // 1-sigma expected move of the underlying over tYears (index points).
    function expectedMove(args) {
        var s = _sigT(args.iv, args.tYears);
        return (args.spot || 0) * s.sigma * s.sqrtT;
    }

    // Underlying price at which a long single-leg option breaks even at expiry.
    function breakeven(args) {
        var type = (args.type || "CE").toUpperCase();
        return type === "PE" ? args.strike - args.premium : args.strike + args.premium;
    }

    // P/L (currency) of a long single-leg position at expiry for a given underlying
    // settle price. lotSize * lots = total units; premium is per unit paid.
    function payoffAtExpiry(pos, spotExp) {
        var type = (pos.type || "CE").toUpperCase();
        var units = (pos.lotSize || 1) * (pos.lots || 1);
        var intrinsic = type === "PE"
            ? Math.max(pos.strike - spotExp, 0)
            : Math.max(spotExp - pos.strike, 0);
        return (intrinsic - pos.premium) * units;
    }

    // Sample the expiry payoff across an underlying range -> [{x, y}] for plotting.
    function payoffCurve(pos, loSpot, hiSpot, steps) {
        steps = steps || 60;
        var out = [];
        for (var i = 0; i <= steps; i++) {
            var x = loSpot + (hiSpot - loSpot) * (i / steps);
            out.push({ x: x, y: payoffAtExpiry(pos, x) });
        }
        return out;
    }

    function _pct(x) { return Math.round(x * 1000) / 10; }        // 0.1234 -> 12.3
    function _r2(x) { return Math.round(x * 100) / 100; }

    /*
     * assess(trade) — the verdict engine used by the UI's verdict card. Pure: no
     * DOM, deterministic. Returns { verdict, tone, reasons[], metrics{} }.
     *
     * trade = {
     *   spot, strike, type ('CE'|'PE'), iv (percent), days,
     *   premium (option entry, per unit — the price you PAY),
     *   slUnderlying, targetUnderlying,   // your plan on the UNDERLYING
     *   lotSize=75, lots=1, r=0.065
     * }
     *
     * Model: project the option's mark if the underlying reaches your target vs
     * your stop, using Black-Scholes with the SAME IV and roughly HALF the time
     * elapsed (intraday move -> some theta paid, but not full expiry). That gives
     * a realistic reward/risk on the OPTION, not just the underlying. POP is the
     * probability the underlying TOUCHES the target before expiry (probTouch).
     */
    function assess(trade) {
        var type = (trade.type || "CE").toUpperCase();
        var days = Math.max(trade.days || 0, 0);
        var tYears = days / 365;
        var r = trade.r == null ? 0.065 : trade.r;
        var lotSize = trade.lotSize || 75;
        var lots = trade.lots || 1;
        var premium = trade.premium;
        var reasons = [];
        var warnings = [];

        // Directional sanity: a CE wants target above spot & SL below; PE the reverse.
        var dirOk = type === "CE"
            ? (trade.targetUnderlying > trade.spot && trade.slUnderlying < trade.spot)
            : (trade.targetUnderlying < trade.spot && trade.slUnderlying > trade.spot);

        // Project option mark at target and at stop (half the time gone).
        var tLeft = Math.max(tYears * 0.5, 0);
        var markAtTarget = bsPrice({ spot: trade.targetUnderlying, strike: trade.strike, iv: trade.iv, tYears: tLeft, type: type, r: r });
        var markAtStop = bsPrice({ spot: trade.slUnderlying, strike: trade.strike, iv: trade.iv, tYears: tLeft, type: type, r: r });

        var reward = (markAtTarget - premium) * lotSize * lots;
        var risk = (premium - markAtStop) * lotSize * lots;
        var rr = risk > 0 ? reward / risk : (reward > 0 ? Infinity : 0);

        var g = bsGreeks({ spot: trade.spot, strike: trade.strike, iv: trade.iv, tYears: tYears, type: type, r: r });
        var thetaPerDay = g.theta * lotSize * lots;              // currency/day (negative)
        var thetaPctOfPrem = premium > 0 ? Math.abs(g.theta) / premium : 0;

        var pop = probTouch({ spot: trade.spot, barrier: trade.targetUnderlying, iv: trade.iv, tYears: tYears, r: r });
        var be = breakeven({ strike: trade.strike, premium: premium, type: type });
        var em = expectedMove({ spot: trade.spot, iv: trade.iv, tYears: tYears });
        var moveNeeded = Math.abs(be - trade.spot);

        // ---- reasons -------------------------------------------------------
        reasons.push("Prob. of touching target before expiry ≈ " + _pct(pop) + "%.");
        reasons.push("Reward:risk on the option ≈ " + (isFinite(rr) ? "1:" + _r2(rr) : "uncapped") +
            " (₹" + Math.round(reward) + " vs ₹" + Math.round(risk) + ").");
        reasons.push("Theta bleeds ≈ " + _pct(thetaPctOfPrem) + "% of premium/day (₹" +
            Math.round(thetaPerDay) + "/day at this size).");
        reasons.push("Breakeven ₹" + _r2(be) + " needs a " + _pct(moveNeeded / trade.spot) +
            "% move; ±1σ by expiry is ≈ " + _r2(em) + " pts (" + _pct(em / trade.spot) + "%).");
        reasons.push("Entry delta ≈ " + _r2(g.delta) + " (≈ " + Math.round(Math.abs(g.delta) * 100) +
            " pts of option move per 100 pts of index).");

        // ---- warnings ------------------------------------------------------
        if (!dirOk) warnings.push("Target/stop are on the wrong side of spot for a " + type + " — check direction.");
        if (thetaPctOfPrem >= 0.08) warnings.push("Heavy theta (≥ 8%/day) — needs the move fast.");
        if (moveNeeded > em) warnings.push("Breakeven is beyond the ±1σ expected move — statistically a stretch.");
        if (Math.abs(g.delta) < 0.2) warnings.push("Deep-OTM (delta < 0.20) — lottery-ticket odds.");

        // ---- verdict -------------------------------------------------------
        var verdict, tone;
        if (!dirOk) {
            verdict = "Check inputs"; tone = "warn";
        } else if (isFinite(rr) && rr >= 2 && pop >= 0.40 && thetaPctOfPrem < 0.08) {
            verdict = "Favorable"; tone = "good";
        } else if (rr >= 1 && pop >= 0.30) {
            verdict = "Marginal"; tone = "warn";
        } else {
            verdict = "Unfavorable"; tone = "bad";
        }
        for (var i = 0; i < warnings.length; i++) reasons.push("⚠ " + warnings[i]);

        return {
            verdict: verdict,
            tone: tone,
            reasons: reasons,
            warnings: warnings,
            metrics: {
                pop: pop, rr: rr, reward: reward, risk: risk,
                thetaPerDay: thetaPerDay, thetaPctOfPrem: thetaPctOfPrem,
                delta: g.delta, gamma: g.gamma, vega: g.vega, theta: g.theta,
                breakeven: be, expectedMove: em, moveNeeded: moveNeeded,
                markAtTarget: markAtTarget, markAtStop: markAtStop
            }
        };
    }

    // Build a strike ladder around spot at a fixed interval (Nifty weekly = 50),
    // ATM +/- n strikes. Returns strikes (numbers) ascending.
    function strikeLadder(spot, interval, n) {
        interval = interval || 50; n = n || 8;
        var atm = Math.round(spot / interval) * interval;
        var out = [];
        for (var k = -n; k <= n; k++) out.push(atm + k * interval);
        return out;
    }

    return {
        normPdf: normPdf,
        normCdf: normCdf,
        bsPrice: bsPrice,
        bsGreeks: bsGreeks,
        probITM: probITM,
        probTouch: probTouch,
        expectedMove: expectedMove,
        breakeven: breakeven,
        payoffAtExpiry: payoffAtExpiry,
        payoffCurve: payoffCurve,
        assess: assess,
        strikeLadder: strikeLadder
    };
});

// ---------------------------------------------------------------------------
// Self-test: `node scripts/options_math.js`. Exits non-zero on any failure so it
// can gate a verify step. No test framework (repo has none for JS).
// ---------------------------------------------------------------------------
if (typeof require !== "undefined" && require.main === module) {
    var M = module.exports;
    var fails = 0, passes = 0;
    function ok(name, cond, detail) {
        if (cond) { passes++; console.log("  PASS  " + name); }
        else { fails++; console.log("  FAIL  " + name + (detail ? "  -> " + detail : "")); }
    }
    function near(a, b, tol) { return Math.abs(a - b) <= (tol == null ? 1e-6 : tol); }

    console.log("options_math.js self-test\n");

    // normCdf anchors
    ok("normCdf(0) == 0.5", near(M.normCdf(0), 0.5, 1e-6), M.normCdf(0));
    ok("normCdf(1.96) ~ 0.975", near(M.normCdf(1.96), 0.975, 1e-3), M.normCdf(1.96));
    ok("normCdf(-1.96) ~ 0.025", near(M.normCdf(-1.96), 0.025, 1e-3), M.normCdf(-1.96));
    ok("normCdf symmetric", near(M.normCdf(0.7) + M.normCdf(-0.7), 1, 1e-6));

    // Put-call parity: C - P = S - K e^{-rT}
    var S = 25000, K = 25000, iv = 13, T = 7 / 365, r = 0.065;
    var C = M.bsPrice({ spot: S, strike: K, iv: iv, tYears: T, type: "CE", r: r });
    var P = M.bsPrice({ spot: S, strike: K, iv: iv, tYears: T, type: "PE", r: r });
    var parity = S - K * Math.exp(-r * T);
    ok("put-call parity holds", near(C - P, parity, 1e-3), "C-P=" + (C - P).toFixed(4) + " vs " + parity.toFixed(4));
    ok("ATM call price positive & sane", C > 0 && C < S * 0.1, C.toFixed(2));

    // Greeks bounds
    var gc = M.bsGreeks({ spot: S, strike: K, iv: iv, tYears: T, type: "CE", r: r });
    var gp = M.bsGreeks({ spot: S, strike: K, iv: iv, tYears: T, type: "PE", r: r });
    ok("call delta in (0,1)", gc.delta > 0 && gc.delta < 1, gc.delta);
    ok("put delta in (-1,0)", gp.delta > -1 && gp.delta < 0, gp.delta);
    ok("delta_CE - delta_PE ~ 1", near(gc.delta - gp.delta, 1, 1e-6));
    ok("call theta negative (long decays)", gc.theta < 0, gc.theta);
    ok("gamma positive", gc.gamma > 0, gc.gamma);
    ok("vega positive & equal CE/PE", gc.vega > 0 && near(gc.vega, gp.vega, 1e-9));

    // probITM / probTouch bounds and ordering
    var pITM = M.probITM({ spot: S, strike: 25200, iv: iv, tYears: T, type: "CE", r: r });
    var pTouch = M.probTouch({ spot: S, barrier: 25200, iv: iv, tYears: T, r: r });
    ok("probITM in [0,1]", pITM >= 0 && pITM <= 1, pITM);
    ok("probTouch in [0,1]", pTouch >= 0 && pTouch <= 1, pTouch);
    ok("touch >= expire-beyond (barrier=target)", pTouch >= pITM - 1e-9, "touch=" + pTouch.toFixed(3) + " itm=" + pITM.toFixed(3));
    // With positive risk-neutral drift (nu = r - sigma^2/2), an up-barrier is
    // slightly EASIER to touch than a symmetric down-barrier — they should be
    // close but ordered, not equal.
    var upT = M.probTouch({ spot: S, barrier: S * 1.02, iv: iv, tYears: T, r: r });
    var dnT = M.probTouch({ spot: S, barrier: S / 1.02, iv: iv, tYears: T, r: r });
    ok("probTouch up >= down & within 5pts (drift)", upT >= dnT && Math.abs(upT - dnT) < 0.05,
        "up=" + upT.toFixed(3) + " dn=" + dnT.toFixed(3));

    // breakeven & payoff
    ok("CE breakeven = strike + premium", near(M.breakeven({ strike: 25000, premium: 120, type: "CE" }), 25120));
    ok("PE breakeven = strike - premium", near(M.breakeven({ strike: 25000, premium: 120, type: "PE" }), 24880));
    var pos = { strike: 25000, premium: 120, type: "CE", lotSize: 75, lots: 1 };
    ok("payoff at deep ITM > 0", M.payoffAtExpiry(pos, 25500) > 0, M.payoffAtExpiry(pos, 25500));
    ok("payoff below strike = -premium*units", near(M.payoffAtExpiry(pos, 24000), -120 * 75), M.payoffAtExpiry(pos, 24000));

    // assess() shape
    var a = M.assess({ spot: 25000, strike: 25100, type: "CE", iv: 13, days: 3, premium: 90, slUnderlying: 24850, targetUnderlying: 25350, lotSize: 75, lots: 1 });
    ok("assess returns a verdict string", typeof a.verdict === "string" && a.verdict.length > 0, a.verdict);
    ok("assess returns >= 1 reason", Array.isArray(a.reasons) && a.reasons.length >= 1, a.reasons.length);
    ok("assess metrics present", a.metrics && typeof a.metrics.pop === "number", JSON.stringify(a.metrics.pop));
    var bad = M.assess({ spot: 25000, strike: 25100, type: "CE", iv: 13, days: 3, premium: 90, slUnderlying: 25200, targetUnderlying: 24800, lotSize: 75, lots: 1 });
    ok("wrong-side inputs flagged", bad.verdict === "Check inputs" && bad.warnings.length >= 1, bad.verdict);

    // strikeLadder
    var lad = M.strikeLadder(25037, 50, 5);
    ok("strikeLadder centers ATM & spans 2n+1", lad.length === 11 && lad.indexOf(25050) >= 0, JSON.stringify(lad));

    console.log("\n" + passes + " passed, " + fails + " failed.");
    process.exit(fails === 0 ? 0 : 1);
}
