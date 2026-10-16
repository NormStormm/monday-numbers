# Data-quality report

**Status: passed.** Numbers published.

| Severity | Check | Detail |
|---|---|---|
| WARN | Missing clock-out | Duke Kamaka (Line Cook), Thu Oct 08 15:36: hours estimated at 8.1 from a typical dinner shift. Have the manager confirm. |
| FIXED | Duplicate POS rows removed | 23 rows re-exported for Wed Sep 16 (identical copies, kept one) |
| FIXED | Duplicate time punches removed | 1 duplicated punch(es), e.g. E104 on Sun Aug 23 |
| FIXED | Employee names standardized | 45 name spellings differed only in capitalization |
| FIXED | Overnight clock-outs corrected | 185 shifts ended after midnight but were exported with the clock-in date; one day added |
| FIXED | POS formats standardized | 1141 rows used MM/DD/YYYY dates and 1141 used "$1,234.50" text amounts (before the POS update); converted |
