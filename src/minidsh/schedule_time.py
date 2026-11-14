import calendar
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


UTC = timezone.utc
WEEKDAYS = {"MO": 1, "TU": 2, "WE": 3, "TH": 4, "FR": 5, "SA": 6, "SU": 7}


class TimeRuleError(ValueError):
    def __init__(self, message: str, code: str = "invalid_rule") -> None:
        super().__init__(message)
        self.code = code


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TimeRuleError("expected an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise TimeRuleError("invalid RFC 3339 timestamp") from exc
    return parsed.astimezone(UTC)


def zone(name: object) -> ZoneInfo:
    if not isinstance(name, str) or not name:
        raise TimeRuleError("time_zone is required", "invalid_time_zone")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise TimeRuleError(f"unknown time zone: {name}", "invalid_time_zone") from exc


def wall_time(value: object) -> Tuple[int, int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,3})?", value):
        raise TimeRuleError("time must be HH:mm:ss with optional milliseconds")
    hour, minute, second = [int(part) for part in value.split(".")[0].split(":")]
    millisecond = int((value.split(".")[1] if "." in value else "0").ljust(3, "0"))
    return hour, minute, second, millisecond


def valid_local(naive: datetime, tz: ZoneInfo) -> Optional[datetime]:
    naive = naive.replace(tzinfo=None)
    candidate = naive.replace(tzinfo=tz, fold=0)
    roundtrip = candidate.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    return candidate if roundtrip == naive else None


def local_datetime(date: datetime, time: Tuple[int, int, int, int], tz: ZoneInfo) -> Optional[datetime]:
    hour, minute, second, millisecond = time
    return valid_local(datetime(date.year, date.month, date.day, hour, minute, second, millisecond * 1000), tz)


def next_daily(time: Tuple[int, int, int, int], tz: ZoneInfo, after: datetime, weekdays: Optional[Set[int]] = None) -> datetime:
    local_after = after.astimezone(tz)
    for offset in range(0, 366 * 400):
        date = local_after + timedelta(days=offset)
        if weekdays is not None and date.isoweekday() not in weekdays:
            continue
        candidate = local_datetime(date, time, tz)
        if candidate is not None and candidate.astimezone(UTC) > after:
            return candidate.astimezone(UTC)
    raise TimeRuleError("rule has no representable future occurrence", "not_future")


def cron_field(text: str, minimum: int, maximum: int, sunday: bool = False) -> Tuple[Set[int], bool]:
    values: Set[int] = set()
    wildcard = text.startswith("*")
    for part in text.split(","):
        match = re.fullmatch(r"(\*|\d+(?:-\d+)?)(?:/(\d+))?", part)
        if not match:
            raise TimeRuleError(f"invalid cron field: {text}")
        base, step_text = match.groups()
        step = int(step_text or "1")
        if step < 1 or (step_text is not None and "/" in base):
            raise TimeRuleError(f"invalid cron step: {part}")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start, end = [int(value) for value in base.split("-")]
            if start > end:
                raise TimeRuleError(f"invalid cron range: {base}")
        else:
            if step_text is not None:
                raise TimeRuleError("a single cron value cannot have a step")
            start = end = int(base)
        if start < minimum or end > maximum:
            raise TimeRuleError(f"cron value out of range: {base}")
        values.update(range(start, end + 1, step))
    if sunday and 7 in values:
        values.remove(7)
        values.add(0)
    return values, wildcard


def next_cron(expression: object, time_zone: object, after: datetime) -> datetime:
    if not isinstance(expression, str):
        raise TimeRuleError("cron expression is required")
    fields = expression.split()
    if len(fields) != 5:
        raise TimeRuleError("cron expression must have five fields")
    minutes, _ = cron_field(fields[0], 0, 59)
    hours, _ = cron_field(fields[1], 0, 23)
    days, days_wild = cron_field(fields[2], 1, 31)
    months, _ = cron_field(fields[3], 1, 12)
    weekdays, weekdays_wild = cron_field(fields[4], 0, 7, True)
    tz = zone(time_zone)
    local = after.astimezone(tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(527040 * 400):
        day_matches = local.day in days
        weekday_matches = (local.isoweekday() % 7) in weekdays
        date_matches = (day_matches and weekday_matches) if days_wild or weekdays_wild else (day_matches or weekday_matches)
        if local.minute in minutes and local.hour in hours and local.month in months and date_matches:
            candidate = valid_local(local, tz)
            if candidate is not None and candidate.astimezone(UTC) > after:
                return candidate.astimezone(UTC)
        local += timedelta(minutes=1)
    raise TimeRuleError("cron has no future occurrence", "not_future")


def rrule_parts(text: object) -> Tuple[datetime, ZoneInfo, str, Dict[str, str]]:
    if not isinstance(text, str) or len(text.splitlines()) != 2:
        raise TimeRuleError("rrule requires exactly DTSTART and RRULE lines")
    start_line, rule_line = text.splitlines()
    match = re.fullmatch(r"DTSTART(?:;TZID=([^:]+))?:(\d{8}T\d{6})(Z)?", start_line)
    if not match:
        raise TimeRuleError("DTSTART must use an IANA TZID or UTC Z")
    zone_name, local_text, zulu = match.groups()
    if zone_name and zulu:
        raise TimeRuleError("DTSTART cannot combine TZID and Z")
    tz = UTC if zulu else zone(zone_name)
    try:
        naive = datetime.strptime(local_text, "%Y%m%dT%H%M%S")
    except ValueError as exc:
        raise TimeRuleError("invalid DTSTART") from exc
    start = valid_local(naive, tz)
    if start is None:
        raise TimeRuleError("DTSTART names a nonexistent local time")
    if not rule_line.startswith("RRULE:"):
        raise TimeRuleError("second line must be RRULE")
    parts: Dict[str, str] = {}
    for item in rule_line[6:].split(";"):
        key, separator, value = item.partition("=")
        if not separator or key in parts or not value:
            raise TimeRuleError("duplicate or invalid RRULE field")
        parts[key] = value
    allowed = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "WKST", "BYSECOND", "BYMINUTE", "BYHOUR", "BYDAY", "BYMONTHDAY", "BYYEARDAY", "BYWEEKNO", "BYMONTH", "BYSETPOS"}
    if set(parts) - allowed or "FREQ" not in parts:
        raise TimeRuleError("unsupported RRULE field")
    if parts["FREQ"] not in {"MINUTELY", "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"}:
        raise TimeRuleError("unsupported RRULE frequency", "frequency_too_high" if parts["FREQ"] == "SECONDLY" else "invalid_rule")
    if "BYWEEKNO" in parts or "BYYEARDAY" in parts:
        raise TimeRuleError("BYWEEKNO and BYYEARDAY are not implemented by this learning runtime")
    frequency = parts["FREQ"]
    by_parts = set(parts) - {"FREQ", "INTERVAL", "COUNT", "UNTIL", "WKST"}
    if frequency in {"MINUTELY", "HOURLY"} and by_parts:
        raise TimeRuleError("MINUTELY and HOURLY accept no BY parts")
    if "BYSECOND" in parts and len(parts["BYSECOND"].split(",")) != 1:
        raise TimeRuleError("BYSECOND accepts one value")
    if "BYSETPOS" in parts and not by_parts - {"BYSETPOS"}:
        raise TimeRuleError("BYSETPOS requires another BY part")
    if "COUNT" in parts and "UNTIL" in parts:
        raise TimeRuleError("COUNT and UNTIL cannot coexist")
    count = int(parts.get("COUNT", "1")) if parts.get("COUNT") == "1" else int(parts.get("COUNT", "0"))
    if "COUNT" in parts and not 1 <= count <= 1000:
        raise TimeRuleError("COUNT must be from 1 through 1000")
    if "INTERVAL" in parts and (not parts["INTERVAL"].isdigit() or int(parts["INTERVAL"]) < 1):
        raise TimeRuleError("INTERVAL must be positive")
    if "UNTIL" in parts:
        until = parts["UNTIL"]
        if not re.fullmatch(r"\d{8}T\d{6}Z", until):
            raise TimeRuleError("UNTIL must be UTC")
        parse_utc(datetime.strptime(until, "%Y%m%dT%H%M%SZ").isoformat().replace("+00:00", "Z"))
    return start, tz, frequency, parts


def integer_list(parts: Dict[str, str], key: str, minimum: int, maximum: int) -> Optional[List[int]]:
    if key not in parts:
        return None
    values: List[int] = []
    for item in parts[key].split(","):
        if not re.fullmatch(r"-?\d+", item):
            raise TimeRuleError(f"invalid {key}")
        value = int(item)
        if not minimum <= value <= maximum or value == 0 and key == "BYSETPOS":
            raise TimeRuleError(f"{key} out of range")
        values.append(value)
    if len(values) != len(set(values)) or len(values) > 31 and key == "BYSETPOS":
        raise TimeRuleError(f"invalid {key} list")
    return values


def byday(parts: Dict[str, str], frequency: str) -> Optional[List[Tuple[int, Optional[int]]]]:
    if "BYDAY" not in parts:
        return None
    if frequency not in {"WEEKLY", "MONTHLY", "YEARLY"}:
        raise TimeRuleError("BYDAY requires WEEKLY, MONTHLY, or YEARLY")
    result: List[Tuple[int, Optional[int]]] = []
    for item in parts["BYDAY"].split(","):
        match = re.fullmatch(r"([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)", item)
        if not match:
            raise TimeRuleError("invalid BYDAY")
        ordinal_text, weekday = match.groups()
        ordinal = int(ordinal_text) if ordinal_text else None
        if ordinal is not None and (frequency == "WEEKLY" or ordinal == 0 or abs(ordinal) > 53):
            raise TimeRuleError("ordinal BYDAY requires MONTHLY or YEARLY")
        result.append((WEEKDAYS[weekday], ordinal))
    return result


def rrule_time(parts: Dict[str, str], start: datetime) -> Tuple[int, int, int, int]:
    hours = integer_list(parts, "BYHOUR", 0, 23) or [start.hour]
    minutes = integer_list(parts, "BYMINUTE", 0, 59) or [start.minute]
    seconds = integer_list(parts, "BYSECOND", 0, 59) or [start.second]
    if len(hours) != 1 or len(minutes) != 1 or len(seconds) != 1:
        raise TimeRuleError("this learning runtime accepts one BYHOUR, BYMINUTE, and BYSECOND value")
    return hours[0], minutes[0], seconds[0], 0


def period_days(start: datetime, frequency: str, index: int, interval: int) -> List[datetime]:
    year, month = start.year, start.month
    if frequency == "DAILY":
        return [start + timedelta(days=index * interval)]
    if frequency == "WEEKLY":
        monday = start - timedelta(days=start.isoweekday() - 1)
        return [monday + timedelta(days=index * interval * 7 + day) for day in range(7)]
    months = (year * 12 + month - 1) + index * interval if frequency == "MONTHLY" else 0
    if frequency == "MONTHLY":
        year, month = divmod(months, 12)
        month += 1
        last = calendar.monthrange(year, month)[1]
        return [datetime(year, month, day) for day in range(1, last + 1)]
    if frequency == "YEARLY":
        year += index * interval
        first = datetime(year, 1, 1)
        return [first + timedelta(days=day) for day in range(366 if calendar.isleap(year) else 365)]
    raise AssertionError(frequency)


def next_rrule(text: str, after: datetime, delivered: int = 0) -> Optional[datetime]:
    start, tz, frequency, parts = rrule_parts(text)
    interval = int(parts.get("INTERVAL", "1"))
    count = int(parts["COUNT"]) if "COUNT" in parts else None
    if count is not None and delivered >= count:
        return None
    until = datetime.strptime(parts["UNTIL"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) if "UNTIL" in parts else None
    selected_days = byday(parts, frequency)
    month_days = integer_list(parts, "BYMONTHDAY", -31, 31)
    months = integer_list(parts, "BYMONTH", 1, 12)
    positions = integer_list(parts, "BYSETPOS", -366, 366)
    time = rrule_time(parts, start)
    base = start.replace(tzinfo=None)
    occurrences_seen = 0
    for index in range(0, 10000):
        if frequency == "MINUTELY":
            candidates = [start + timedelta(minutes=index * interval)]
        elif frequency == "HOURLY":
            candidates = [start + timedelta(hours=index * interval)]
        else:
            days = period_days(base, frequency, index, interval)
            filtered: List[datetime] = []
            for day in days:
                if months is not None and day.month not in months:
                    continue
                if frequency == "WEEKLY" and selected_days is None and day.isoweekday() != start.isoweekday():
                    continue
                if frequency == "MONTHLY" and month_days is None and selected_days is None and day.day != start.day:
                    continue
                if frequency == "YEARLY" and months is None and day.month != start.month:
                    continue
                if frequency == "YEARLY" and month_days is None and selected_days is None and day.day != start.day:
                    continue
                if month_days is not None:
                    last = calendar.monthrange(day.year, day.month)[1]
                    accepted = {value if value > 0 else last + value + 1 for value in month_days}
                    if day.day not in accepted:
                        continue
                if selected_days is not None:
                    matched = False
                    for weekday, ordinal in selected_days:
                        if day.isoweekday() != weekday:
                            continue
                        if ordinal is None:
                            matched = True
                        elif frequency == "MONTHLY":
                            occurrence = (day.day - 1) // 7 + 1
                            last_occurrence = (calendar.monthrange(day.year, day.month)[1] - day.day) // 7 + 1
                            matched = occurrence == ordinal or (ordinal < 0 and last_occurrence == -ordinal)
                        else:
                            occurrence = (day.timetuple().tm_yday - 1) // 7 + 1
                            remaining = (datetime(day.year, 12, 31).timetuple().tm_yday - day.timetuple().tm_yday) // 7 + 1
                            matched = occurrence == ordinal or (ordinal < 0 and remaining == -ordinal)
                        if matched:
                            break
                    if not matched:
                        continue
                filtered.append(day)
            if positions is not None:
                filtered = [filtered[position - 1 if position > 0 else position] for position in positions if -len(filtered) <= (position - 1 if position > 0 else position) < len(filtered)]
            candidates = []
            for day in filtered:
                candidate = local_datetime(day, time, tz)
                if candidate is not None:
                    candidates.append(candidate)
            if len(candidates) > 10080:
                raise TimeRuleError("too many candidates in one recurrence period")
        seen: Set[datetime] = set()
        for candidate in sorted(set(candidates)):
            instant = candidate.astimezone(UTC)
            if instant in seen or instant < start:
                continue
            seen.add(instant)
            if until is not None and instant > until:
                return None
            occurrences_seen += 1
            if count is not None and occurrences_seen > count:
                return None
            if instant <= after:
                continue
            return instant
        if until is not None and candidates and min(candidate.astimezone(UTC) for candidate in candidates) > until:
            return None
    raise TimeRuleError("recurrence search exceeded its bound")


def next_occurrence(record: Dict[str, object], after: datetime, delivered: int = 0) -> Optional[datetime]:
    kind = record["kind"]
    if kind in {"at", "after"}:
        target = parse_utc(str(record["scheduledAt"]))
        return target if target > after else None
    if kind == "every":
        interval = int(record["everySeconds"])
        if interval < 60:
            raise TimeRuleError("everySeconds must be at least 60")
        scheduled = parse_utc(str(record["scheduledAt"]))
        if scheduled > after:
            return scheduled
        elapsed = (after - scheduled).total_seconds()
        return scheduled + timedelta(seconds=(int(elapsed // interval) + 1) * interval)
    if kind in {"daily", "weekly"}:
        weekdays = set(int(day) for day in record.get("weekdays", [])) if kind == "weekly" else None
        if kind == "weekly" and not weekdays:
            raise TimeRuleError("weekly requires weekdays")
        return next_daily(wall_time(record["time"]), zone(record["timeZone"]), after, weekdays)
    if kind == "cron":
        return next_cron(record["expression"], record["timeZone"], after)
    if kind == "rrule":
        return next_rrule(str(record["rrule"]), after, delivered)
    raise TimeRuleError(f"unknown schedule kind: {kind}")
