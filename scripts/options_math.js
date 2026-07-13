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

    function _forwardOf(args, r, tYears) {
        if (args.forward != null) return args.forward;
        return (args.spot || 0) * Math.exp(r * Math.max(tYears, 0));
    }

    // B1: forward via ATM put-call parity: F = K_atm + (C_atm - P_atm)*e^{rT}.
    // NIFTY weeklies have no tradeable futures ("matching-expiry futures price"
    // is unfetchable), so the forward is imputed from the chain's own ATM CE/PE
    // ltp instead. Falls back to the pure carry forward (spot * e^{rT}) when the
    // ATM ltps aren't available (mock provider, stale leg, manual entry).
    function impliedForward(args) {
        var atmStrike = args.atmStrike, tYears = args.tYears;
        var r = args.r == null ? 0.065 : args.r;
        var callLtp = args.atmCallLtp, putLtp = args.atmPutLtp;
        var growth = Math.exp(r * Math.max(tYears, 0));
        var spotFallback = (args.spot || 0) * growth;
        if (atmStrike == null || callLtp == null || putLtp == null || !(callLtp > 0) || !(putLtp > 0)) {
            return { forward: spotFallback, source: "spot-fallback" };
        }
        return { forward: atmStrike + (callLtp - putLtp) * growth, source: "parity" };
    }

    // Black-76 d1/d2 on the FORWARD (not spot): d1 = [ln(F/K) + sigma^2 T/2] / (sigma sqrt T).
    // No separate "+r*T" drift term here -- the forward already prices in the
    // carry, unlike plain Black-Scholes where d1 carries that term against spot.
    function _d1d2F(forward, strike, iv, tYears) {
        var s = _sigT(iv, tYears);
        if (s.denom <= 1e-12 || forward <= 0 || strike <= 0) {
            // At/after expiry (or zero vol): d1/d2 collapse to ±inf by moneyness.
            var m = Math.log((forward || 1e-9) / (strike || 1e-9));
            var big = m >= 0 ? 40 : -40;
            return { d1: big, d2: big, sigma: s.sigma, sqrtT: s.sqrtT };
        }
        var d1 = (Math.log(forward / strike) + 0.5 * s.sigma * s.sigma * tYears) / s.denom;
        var d2 = d1 - s.denom;
        return { d1: d1, d2: d2, sigma: s.sigma, sqrtT: s.sqrtT };
    }

    // Black-76 fair value (per unit of underlying). Pass `forward` directly when
    // you have a parity-implied one (see impliedForward); otherwise pass `spot`
    // (+ optional `r`) and the pure carry forward spot*e^{rT} is used, which
    // makes this numerically IDENTICAL to the old plain Black-Scholes price
    // (the standard BS <-> Black-76 equivalence) -- existing spot-only callers
    // are unaffected.
    function bsPrice(args) {
        var strike = args.strike, tYears = args.tYears;
        var type = (args.type || "CE").toUpperCase();
        var r = args.r == null ? 0.065 : args.r;
        var F = _forwardOf(args, r, tYears);
        var x = _d1d2F(F, strike, args.iv, tYears);
        var disc = Math.exp(-r * Math.max(tYears, 0));
        if (type === "PE") {
            return Math.max(disc * (strike * normCdf(-x.d2) - F * normCdf(-x.d1)), 0);
        }
        return Math.max(disc * (F * normCdf(x.d1) - strike * normCdf(x.d2)), 0);
    }

    // Greeks, Black-76 form. delta now carries the discount factor e^{-rT} (a
    // deliberate change from the old plain-BS delta, which had none -- this IS
    // the correct forward-measure delta). gamma/vega are on the forward; theta
    // = -disc*F*phi(d1)*sigma/(2 sqrt T) + r*price, derived from d(price)/dT
    // holding F fixed (the standard practitioner "sticky forward" theta) --
    // differs slightly from the old spot-based theta by design.
    function bsGreeks(args) {
        var strike = args.strike, tYears = args.tYears;
        var type = (args.type || "CE").toUpperCase();
        var r = args.r == null ? 0.065 : args.r;
        var F = _forwardOf(args, r, tYears);
        var x = _d1d2F(F, strike, args.iv, tYears);
        var disc = Math.exp(-r * Math.max(tYears, 0));
        var pdf = normPdf(x.d1);
        var sqrtT = x.sqrtT || 1e-9;
        var gamma = (disc * pdf) / ((F * x.sigma * sqrtT) || 1e-9);
        var vegaAnnual = disc * F * pdf * sqrtT;                    // per 1.00 (=100%) of sigma
        var thetaTerm = -(disc * F * pdf * x.sigma) / (2 * sqrtT);  // shared theta term (annual)
        var delta, price;
        if (type === "PE") {
            delta = -disc * normCdf(-x.d1);
            price = Math.max(disc * (strike * normCdf(-x.d2) - F * normCdf(-x.d1)), 0);
        } else {
            delta = disc * normCdf(x.d1);
            price = Math.max(disc * (F * normCdf(x.d1) - strike * normCdf(x.d2)), 0);
        }
        var thetaAnnual = thetaTerm + r * price;
        return {
            delta: delta,
            gamma: gamma,
            theta: thetaAnnual / 365,   // per calendar day
            vega: vegaAnnual / 100      // per 1 IV percentage-point
        };
    }

    // Risk-neutral probability the option expires in-the-money = N(d2) for a call,
    // N(-d2) for a put. (Black-76 form; forward defaults to spot*e^{rT}.)
    function probITM(args) {
        var r = args.r == null ? 0.065 : args.r;
        var type = (args.type || "CE").toUpperCase();
        var F = _forwardOf(args, r, args.tYears);
        var x = _d1d2F(F, args.strike, args.iv, args.tYears);
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

        // B2: market IV (from the live chain leg) replaces the manual/BS-implied
        // IV whenever it's usable.
        var effIv = (trade.marketIv != null && trade.marketIv > 0) ? trade.marketIv : trade.iv;

        // B3: intraday holding horizon. The assessor page defaults this to "time
        // left in today's session"; when the caller omits it we assume a full
        // hold to expiry (days) -- the old, conservative behaviour.
        var horizonDaysIn = trade.horizonDays == null ? days : trade.horizonDays;
        var horizonDays = Math.min(Math.max(horizonDaysIn, 0), days);
        var horizonTYears = horizonDays / 365;
        var tLeft = Math.max(tYears - horizonTYears, 0);   // time-to-expiry REMAINING once the hold elapses

        // B1: forward via ATM put-call parity for the ENTRY greeks (falls back to
        // spot*e^{rT} when no live ATM CE/PE ltp is supplied).
        var fwd = impliedForward({
            spot: trade.spot, atmStrike: trade.atmStrike != null ? trade.atmStrike : trade.strike,
            atmCallLtp: trade.atmCallLtp, atmPutLtp: trade.atmPutLtp, tYears: tYears, r: r
        });

        // Project option mark at target and at stop, decayed by the ACTUAL
        // holding window (tLeft), not "half the time to expiry" (the old,
        // arbitrary assumption that ignored the intraday-exit reality).
        var markAtTarget = bsPrice({ spot: trade.targetUnderlying, strike: trade.strike, iv: effIv, tYears: tLeft, type: type, r: r });
        var markAtStop = bsPrice({ spot: trade.slUnderlying, strike: trade.strike, iv: effIv, tYears: tLeft, type: type, r: r });

        var reward = (markAtTarget - premium) * lotSize * lots;
        var risk = (premium - markAtStop) * lotSize * lots;
        var rr = risk > 0 ? reward / risk : (reward > 0 ? Infinity : 0);

        // B2: market greeks (straight from the chain leg) replace the
        // BS-computed ones field-by-field whenever finite; BS fallback
        // (using the parity forward) otherwise (mock provider / stale leg).
        var bsG = bsGreeks({ forward: fwd.forward, strike: trade.strike, iv: effIv, tYears: tYears, type: type, r: r });
        var mg = trade.marketGreeks || {};
        var g = {
            delta: isFinite(mg.delta) ? mg.delta : bsG.delta,
            gamma: isFinite(mg.gamma) ? mg.gamma : bsG.gamma,
            theta: isFinite(mg.theta) ? mg.theta : bsG.theta,
            vega: isFinite(mg.vega) ? mg.vega : bsG.vega
        };
        var thetaPerDay = g.theta * lotSize * lots;              // currency/day (negative)
        var thetaPctOfPrem = premium > 0 ? Math.abs(g.theta) / premium : 0;   // legacy display metric

        // B3: theta gate over the ACTUAL hold, as a fraction of the projected
        // reward -- replaces the old absolute "<8%/day" gate, which no weekly
        // ATM option could ever pass (theta routinely 15-25%/day of premium;
        // the trade never holds for a full day anyway).
        var thetaCostHorizon = Math.abs(thetaPerDay) * horizonDays;
        var thetaCostPctOfReward = reward > 0 ? thetaCostHorizon / reward : (thetaCostHorizon > 0 ? Infinity : 0);

        // B3: PoP is the probability of touching the target WITHIN the horizon
        // (not the full days-to-expiry -- the trade is flattened EOD).
        var pop = probTouch({ spot: trade.spot, barrier: trade.targetUnderlying, iv: effIv, tYears: horizonTYears, r: r });
        var be = breakeven({ strike: trade.strike, premium: premium, type: type });
        var em = expectedMove({ spot: trade.spot, iv: effIv, tYears: tYears });
        var moveNeeded = Math.abs(be - trade.spot);

        // ---- reasons -------------------------------------------------------
        reasons.push("Forward ≈ " + _r2(fwd.forward) + " (" + fwd.source +
            (fwd.source === "parity" ? " — ATM put-call parity" : " — spot·e^{rT}") + ").");
        reasons.push("Prob. of touching target within the " + _r2(horizonDays) + "-day hold ≈ " + _pct(pop) + "%.");
        reasons.push("Reward:risk on the option ≈ " + (isFinite(rr) ? "1:" + _r2(rr) : "uncapped") +
            " (₹" + Math.round(reward) + " vs ₹" + Math.round(risk) + ").");
        reasons.push("Theta cost over the hold ≈ " + _pct(thetaCostPctOfReward) + "% of projected reward (₹" +
            Math.round(thetaCostHorizon) + "; " + _pct(thetaPctOfPrem) + "%/day of premium).");
        reasons.push("Breakeven ₹" + _r2(be) + " needs a " + _pct(moveNeeded / trade.spot) +
            "% move; ±1σ by expiry is ≈ " + _r2(em) + " pts (" + _pct(em / trade.spot) + "%).");
        reasons.push("Entry delta ≈ " + _r2(g.delta) + " (≈ " + Math.round(Math.abs(g.delta) * 100) +
            " pts of option move per 100 pts of index)" +
            (isFinite(mg.delta) ? " — chain greek" : " — BS estimate") + ".");

        // ---- warnings ------------------------------------------------------
        if (!dirOk) warnings.push("Target/stop are on the wrong side of spot for a " + type + " — check direction.");
        if (thetaCostPctOfReward > 0.25) warnings.push("Theta over the hold eats > 25% of projected reward — needs the move fast.");
        if (moveNeeded > em) warnings.push("Breakeven is beyond the ±1σ expected move — statistically a stretch.");
        if (Math.abs(g.delta) < 0.2) warnings.push("Deep-OTM (delta < 0.20) — lottery-ticket odds.");

        // ---- verdict -------------------------------------------------------
        var verdict, tone;
        if (!dirOk) {
            verdict = "Check inputs"; tone = "warn";
        } else if (isFinite(rr) && rr >= 2 && pop >= 0.40 && thetaCostPctOfReward <= 0.25) {
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
                thetaPerDay: thetaPerDay, thetaPctOfPrem: thetaPctOfPrem, thetaCostPctOfReward: thetaCostPctOfReward,
                delta: g.delta, gamma: g.gamma, vega: g.vega, theta: g.theta,
                breakeven: be, expectedMove: em, moveNeeded: moveNeeded,
                markAtTarget: markAtTarget, markAtStop: markAtStop,
                forward: fwd.forward, forwardSource: fwd.source, horizonDays: horizonDays
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
        impliedForward: impliedForward,
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
    // B1: Black-76 delta carries the discount factor e^{-rT} (delta_CE - delta_PE
    // = disc*(N(d1)+N(-d1)) = disc*1 = e^{-rT}), unlike old plain-BS delta which
    // had no discounting and summed to exactly 1.
    ok("delta_CE - delta_PE ~ e^-rT (Black-76 discounting)", near(gc.delta - gp.delta, Math.exp(-r * T), 1e-6),
        (gc.delta - gp.delta) + " vs " + Math.exp(-r * T));
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

    // --- B1: impliedForward --- (returns {forward, source})
    var fwdCP = M.impliedForward({ atmStrike: 25000, atmCallLtp: 130, atmPutLtp: 100, tYears: 7 / 365, r: 0.065, spot: 25000 });
    var expectFwd = 25000 + (130 - 100) * Math.exp(0.065 * 7 / 365);
    ok("impliedForward from C-P parity", near(fwdCP.forward, expectFwd, 1e-6) && fwdCP.source === "parity", fwdCP.forward + " vs " + expectFwd);
    var fwdFallback = M.impliedForward({ atmStrike: 25000, atmCallLtp: 0, atmPutLtp: 100, tYears: 7 / 365, r: 0.065, spot: 24950 });
    ok("impliedForward falls back to spot*e^rT when ltp missing", near(fwdFallback.forward, 24950 * Math.exp(0.065 * 7 / 365), 1e-6) && fwdFallback.source === "spot-fallback", fwdFallback.forward);
    var fwdFallback2 = M.impliedForward({ atmStrike: 25000, tYears: 7 / 365, r: 0.065, spot: 24950 });
    ok("impliedForward falls back when ltps absent entirely", near(fwdFallback2.forward, 24950 * Math.exp(0.065 * 7 / 365), 1e-6) && fwdFallback2.source === "spot-fallback", fwdFallback2.forward);

    // Round-trip: given a forward F, price call/put off F, feed those ltps back in, recover F within 1e-6
    var Frt = 25137.42, Krt = 25100, ivRt = 14, Trt = 5 / 365;
    var Crt = M.bsPrice({ strike: Krt, iv: ivRt, tYears: Trt, type: "CE", r: r, forward: Frt });
    var Prt = M.bsPrice({ strike: Krt, iv: ivRt, tYears: Trt, type: "PE", r: r, forward: Frt });
    var Frecovered = M.impliedForward({ atmStrike: Krt, atmCallLtp: Crt, atmPutLtp: Prt, tYears: Trt, r: r, spot: Frt }).forward;
    ok("impliedForward round-trips through Black-76 pricer", near(Frecovered, Frt, 1e-6), Frecovered + " vs " + Frt);

    // --- B1: Black-76 delta bounds respect discount factor (not raw N(d1)) ---
    var gcF = M.bsGreeks({ spot: S, strike: K, iv: iv, tYears: T, type: "CE", r: r, forward: S * Math.exp(r * T) });
    var discFactor = Math.exp(-r * T);
    ok("Black-76 call delta = e^-rT * N(d1) (< raw N(d1))", gcF.delta < M.normCdf(1) && gcF.delta > 0 && near(gcF.delta / discFactor, M.normCdf((Math.log((S * Math.exp(r * T)) / K) + 0.5 * (iv / 100) * (iv / 100) * T) / ((iv / 100) * Math.sqrt(T))), 1e-6), gcF.delta);

    // --- B2: assess() horizonDays shortens PoP vs full days-to-expiry ---
    var aFullDays = M.assess({ spot: 25000, strike: 25100, type: "CE", iv: 15, days: 5, premium: 90, slUnderlying: 24850, targetUnderlying: 25350, lotSize: 75, lots: 1, horizonDays: 5 });
    var aShortHorizon = M.assess({ spot: 25000, strike: 25100, type: "CE", iv: 15, days: 5, premium: 90, slUnderlying: 24850, targetUnderlying: 25350, lotSize: 75, lots: 1, horizonDays: 0.25 });
    ok("pop with 0.25-day horizon < pop with 5-day tYears", aShortHorizon.metrics.pop < aFullDays.metrics.pop,
        "short=" + aShortHorizon.metrics.pop.toFixed(4) + " full=" + aFullDays.metrics.pop.toFixed(4));

    // --- B3: theta gate must be reachable for a weekly-ATM-like case with decent reward ---
    // Old absolute gate was thetaPctOfPrem < 0.08 (theta/day as % of premium), which
    // is routinely 15-25%/day for weekly ATM options -> can NEVER pass. New gate is
    // thetaCostPctOfReward <= 0.25 (theta cost over the ACTUAL hold as % of projected
    // reward). Confirm: legacy metric is deep in "impossible" territory (>25%/day)
    // while the new hold-scoped gate is comfortably passable.
    var weeklyAtm = M.assess({ spot: 25000, strike: 25000, type: "CE", iv: 12, days: 1, premium: 45, slUnderlying: 24940, targetUnderlying: 25120, lotSize: 75, lots: 1, horizonDays: 0.4 });
    ok("legacy thetaPctOfPrem/day would have failed the OLD <0.08 gate (proves bug existed)",
        weeklyAtm.metrics.thetaPctOfPrem >= 0.08, weeklyAtm.metrics.thetaPctOfPrem.toFixed(3));
    ok("new hold-scoped theta gate (thetaCostPctOfReward <= 0.25) passes for this case",
        weeklyAtm.metrics.thetaCostPctOfReward <= 0.25 && isFinite(weeklyAtm.metrics.rr) && weeklyAtm.metrics.rr >= 2,
        "thetaCostPctOfReward=" + weeklyAtm.metrics.thetaCostPctOfReward.toFixed(3) + " rr=" + weeklyAtm.metrics.rr.toFixed(2));
    // With a higher-pop, closer target (tight enough to keep theta cost under
    // 25% of reward) the same weekly-ATM case should actually reach Favorable,
    // proving Favorable is populated at all (not just theoretically reachable).
    var weeklyAtmCloser = M.assess({ spot: 25000, strike: 25000, type: "CE", iv: 10, days: 1, premium: 40, slUnderlying: 24975, targetUnderlying: 25045, lotSize: 75, lots: 1, horizonDays: 0.3 });
    ok("weekly-ATM-like case with a reachable target hits Favorable",
        weeklyAtmCloser.verdict === "Favorable",
        weeklyAtmCloser.verdict + " pop=" + weeklyAtmCloser.metrics.pop.toFixed(3) + " rr=" + weeklyAtmCloser.metrics.rr.toFixed(2) + " thetaCostPctOfReward=" + weeklyAtmCloser.metrics.thetaCostPctOfReward.toFixed(3));

    console.log("\n" + passes + " passed, " + fails + " failed.");
    process.exit(fails === 0 ? 0 : 1);
}
