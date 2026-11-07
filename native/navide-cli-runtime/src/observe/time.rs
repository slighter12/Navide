//! ISO 8601 UTC spelling of an epoch time, the way the baseline readers print one (`isoformat()` of an aware UTC datetime).

/// A UTC calendar time: the proleptic Gregorian date and the time of day, as `gmtime` spells them.
struct Civil {
    year: i64,
    month: i64,
    day: i64,
    hour: i64,
    minute: i64,
    second: i64,
}

/// The broken-down UTC time of `seconds` (days since the epoch to a civil date: the proleptic Gregorian calendar every spelling here
/// shares, the same on every platform), `None` where the year does not fit the `int` a C `tm` keeps it in (`gmtime_r` refuses those).
fn civil(seconds: i64) -> Option<Civil> {
    let (days, of_day) = (seconds.div_euclid(86_400), seconds.rem_euclid(86_400));
    // Days from 0000-03-01, so that the leap day is the last day of a year; a 400-year era is 146,097 days.
    let shifted = days + 719_468;
    let (era, day_of_era) = (shifted.div_euclid(146_097), shifted.rem_euclid(146_097));
    let year_of_era = (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_from_march = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_from_march + 2) / 5 + 1;
    let month = if month_from_march < 10 { month_from_march + 3 } else { month_from_march - 9 };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    i32::try_from(year - 1900).ok()?;
    Some(Civil { year, month, day, hour: of_day / 3_600, minute: of_day % 3_600 / 60, second: of_day % 60 })
}

/// `2023-11-14T22:13:20+00:00`, or `2023-11-14T22:13:20.123000+00:00` when `micros` is not 0; `""` where the year is
/// outside 1..=9999 (a date Python cannot build).
pub fn iso_utc(seconds: i64, micros: u32) -> String {
    let Some(time) = civil(seconds).filter(|time| (1..=9999).contains(&time.year)) else {
        return String::new();
    };
    let fraction = if micros == 0 { String::new() } else { format!(".{micros:06}") };
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}{}+00:00",
        time.year, time.month, time.day, time.hour, time.minute, time.second, fraction
    )
}

/// The same for a millisecond stamp.
pub fn iso_millis(milliseconds: i64) -> String {
    iso_utc(milliseconds.div_euclid(1000), (milliseconds.rem_euclid(1000) * 1000) as u32)
}

/// Epoch seconds that may carry a fraction as whole microseconds: Python's `datetime.fromtimestamp` rounds the fraction half to even.
pub fn epoch_micros(seconds: f64) -> i64 {
    let whole = seconds.trunc();
    (whole as i64).saturating_mul(1_000_000).saturating_add(((seconds - whole) * 1e6).round_ties_even() as i64)
}

/// The same for microseconds since the epoch, before 1970 included. A fraction that rounds up to a whole second carries into the
/// seconds, and one below zero borrows from them.
pub fn iso_micros(micros: i64) -> String {
    iso_utc(micros.div_euclid(1_000_000), micros.rem_euclid(1_000_000) as u32)
}

/// The same for epoch seconds that may carry a fraction, before 1970 included (as Python's `_epoch_to_iso` prints them); `""`
/// where the time is not finite or Python cannot build the date.
pub fn iso_seconds(seconds: f64) -> String {
    if !seconds.is_finite() {
        return String::new();
    }
    iso_micros(epoch_micros(seconds))
}

/// `time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + ".mmmZ"`; `""` for a zero stamp (Grok's `_iso`).
pub fn stamp_millis(seconds: f64) -> String {
    if seconds == 0.0 {
        return String::new();
    }
    let whole = seconds.floor();
    let millis = ((seconds - whole) * 1000.0) as i64;
    let Some(time) = civil(whole as i64) else {
        return String::new();
    };
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{millis:03}Z",
        time.year, time.month, time.day, time.hour, time.minute, time.second
    )
}

/// A microsecond stamp a record holds, as Python's `datetime.fromtimestamp(int(value) / 1_000_000, utc).isoformat()` prints it: a bool
/// is 0 or 1, a number is truncated, a string is parsed as an integer; anything else, and a date Python cannot build, is `""`.
pub fn iso_json_micros(value: &serde_json::Value) -> String {
    use serde_json::Value;
    let micros = match value {
        Value::Bool(flag) => i64::from(*flag),
        Value::Number(number) => match number.as_i64().or_else(|| number.as_f64().filter(|float| float.is_finite()).map(|float| float.trunc() as i64)) {
            Some(micros) => micros,
            None => return String::new(),
        },
        Value::String(text) => match text.trim().parse::<i64>() {
            Ok(micros) => micros,
            Err(_) => return String::new(),
        },
        _ => return String::new(),
    };
    iso_seconds(micros as f64 / 1_000_000.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The calendar is arithmetic, so it is the same on every platform (Windows has no `gmtime_r`). The C library is the oracle on the
    /// platforms that have it: every second around each end of the calendar, every day boundary of a few centuries (leap days of century
    /// years included), and a wide pseudo-random sweep, down to the seconds where the C `tm` refuses the year.
    #[cfg(unix)]
    #[test]
    fn the_calendar_agrees_with_the_c_library_where_it_has_one() {
        fn oracle(seconds: i64) -> Option<(i64, i64, i64, i64, i64, i64)> {
            let mut time: libc::tm = unsafe { std::mem::zeroed() };
            (!unsafe { libc::gmtime_r(&seconds, &mut time) }.is_null()).then(|| {
                (i64::from(time.tm_year) + 1900, i64::from(time.tm_mon) + 1, i64::from(time.tm_mday), i64::from(time.tm_hour), i64::from(time.tm_min), i64::from(time.tm_sec))
            })
        }
        fn mine(seconds: i64) -> Option<(i64, i64, i64, i64, i64, i64)> {
            civil(seconds).map(|time| (time.year, time.month, time.day, time.hour, time.minute, time.second))
        }
        let mut probes: Vec<i64> = Vec::new();
        // The last second of the year a `tm` can hold (`tm_year` is `i32::MAX`) and the first of the earliest one.
        for edge in [-62_135_596_800, 0, 951_782_400, 4_107_542_400, 253_402_300_800, 67_768_036_191_676_799, -67_768_040_609_740_800] {
            probes.extend((-3..=3).map(|step| edge + step * 86_399 + step));
        }
        probes.extend((-400 * 366..400 * 366).map(|day| day * 86_400 + 86_399));
        probes.extend((-400 * 366..400 * 366).map(|day| day * 86_400));
        let mut state: u64 = 0x9E37_79B9_7F4A_7C15;
        for _ in 0..200_000 {
            state = state.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1_442_695_040_888_963_407);
            probes.push((state >> 20) as i64 % 70_000_000_000_000 - 35_000_000_000_000);
            probes.push((state >> 33) as i64 % 12_000_000_000 - 6_000_000_000);
        }
        probes.extend([i64::MIN, i64::MIN + 1, i64::MAX, i64::MAX - 1]);
        for seconds in probes {
            assert_eq!(mine(seconds), oracle(seconds), "the calendar disagrees with gmtime_r at {seconds}");
        }
    }

    #[test]
    fn a_stamp_is_the_python_strftime_with_milliseconds_and_a_zero_one_is_empty() {
        assert_eq!(stamp_millis(1_700_000_000.5), "2023-11-14T22:13:20.500Z");
        assert_eq!(stamp_millis(0.0), "");
    }

    /// t36m Z4 (t36l A4): the date comes from the same conversion as every other stamp here. Python's `time.gmtime` floors a fractional time
    /// (`-1.5` is second `-2`), and the milliseconds are the fraction `seconds % 1` it leaves (`.500`): a time before 1970 prints the date it
    /// is. This pins the spelling before and after `stamp_millis` stopped carrying a civil-date algorithm of its own.
    #[test]
    fn a_stamp_before_1970_and_at_the_edges_of_the_calendar_prints_the_date_it_is() {
        assert_eq!(stamp_millis(-1.5), "1969-12-31T23:59:58.500Z");
        assert_eq!(stamp_millis(-1.0), "1969-12-31T23:59:59.000Z");
        assert_eq!(stamp_millis(-86_400.25), "1969-12-30T23:59:59.750Z");
        assert_eq!(stamp_millis(-62_135_596_800.0), "0001-01-01T00:00:00.000Z");
        assert_eq!(stamp_millis(951_782_400.0), "2000-02-29T00:00:00.000Z", "a leap day of a century year");
        assert_eq!(stamp_millis(1_709_164_800.999), "2024-02-29T00:00:00.999Z");
        assert_eq!(stamp_millis(253_402_300_799.5), "9999-12-31T23:59:59.500Z");
        // 253402300799.999 is not representable: its fraction is .99899..., and the milliseconds are that fraction truncated, as Python's
        // `int((seconds % 1) * 1000)` truncates it. The retired civil-date algorithm printed `.998` here too (its run is in t36m/red/z4-*).
        assert_eq!(stamp_millis(253_402_300_799.999), "9999-12-31T23:59:59.998Z");
    }

    #[test]
    fn a_stamp_prints_like_isoformat_and_an_impossible_date_prints_nothing() {
        assert_eq!(iso_millis(1_700_000_000_123), "2023-11-14T22:13:20.123000+00:00");
        assert_eq!(iso_seconds(1_700_000_000.0), "2023-11-14T22:13:20+00:00");
        assert_eq!(iso_utc(i64::MAX, 0), "");
    }

    #[test]
    fn a_fractional_epoch_prints_like_pythons_fromtimestamp() {
        assert_eq!(iso_seconds(1.0), "1970-01-01T00:00:01+00:00");
        assert_eq!(iso_seconds(1.001), "1970-01-01T00:00:01.001000+00:00");
        assert_eq!(iso_seconds(1_792_513_596.0), "2026-10-20T16:26:36+00:00");
        // The fraction rounds half to even and a fraction that rounds up carries into the seconds.
        assert_eq!(iso_seconds(1.999_999_6), "1970-01-01T00:00:02+00:00");
        assert_eq!(iso_seconds(253_402_300_800.0), "");
        assert_eq!(iso_seconds(f64::NAN), "");
    }

    /// Python's `_epoch_to_iso` and the retired `epoch_iso` print a time before 1970 as the date it is; only a time outside
    /// years 1..9999 prints nothing.
    #[test]
    fn a_negative_epoch_prints_the_date_before_1970_like_pythons_fromtimestamp() {
        assert_eq!(iso_seconds(-1.0), "1969-12-31T23:59:59+00:00");
        assert_eq!(iso_seconds(-1.5), "1969-12-31T23:59:58.500000+00:00");
        assert_eq!(iso_seconds(-86_400.0), "1969-12-31T00:00:00+00:00");
        assert_eq!(iso_seconds(-62_135_596_800.0), "0001-01-01T00:00:00+00:00");
        assert_eq!(iso_seconds(-62_135_596_801.0), "");
        // A fraction that rounds up to a whole second carries into the seconds, below zero as above it.
        assert_eq!(iso_seconds(-0.000_000_4), "1970-01-01T00:00:00+00:00");
    }
}
