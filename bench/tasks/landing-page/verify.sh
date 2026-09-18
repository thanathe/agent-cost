#!/usr/bin/env bash
# Mechanical spec check for the landing-page task. Run from inside the artifact dir.
# Exits 0 only if every REQUIRED check passes. Prints one line per check.
f=index.html
fail=0
chk() { # name, condition-result(0/1)
  if [ "$2" -eq 0 ]; then printf '  PASS  %s\n' "$1"; else printf '  FAIL  %s\n' "$1"; fail=1; fi
}
soft() { if [ "$2" -eq 0 ]; then printf '  ok    %s\n' "$1"; else printf '  warn  %s\n' "$1"; fi; }

[ -f "$f" ] || { echo "  FAIL  index.html missing"; exit 1; }
lc=$(tr 'A-Z' 'a-z' < "$f")
# tag-level checks: collapse newlines so multi-line attribute lists still match
flat=$(tr '\n' ' ' <<<"$lc" | tr -s ' ')

chk "index.html exists and is >3KB"            "$([ "$(wc -c < "$f")" -gt 3000 ] && echo 0 || echo 1)"
chk "only index.html created"                  "$([ "$(ls -A | grep -vc '^index.html$')" -eq 0 ] && echo 0 || echo 1)"
chk "exactly one <h1>"                         "$([ "$(grep -o '<h1' <<<"$flat" | wc -l | tr -d ' ')" -eq 1 ] && echo 0 || echo 1)"
chk "no external JS (<script src=http)"        "$(grep -qE '<script[^>]+src=["'\'']?https?:' <<<"$flat" && echo 1 || echo 0)"
chk "no external CSS except google fonts"      "$(grep -oE '<link[^>]+href=["'\'']?https?://[^"'\'' >]+' <<<"$flat" | grep -qvE 'fonts\.(googleapis|gstatic)\.com' && echo 1 || echo 0)"
chk "CSS custom properties used"               "$(grep -qE '^\s*--[a-z0-9-]+\s*:' <<<"$lc" && echo 0 || echo 1)"
chk "prefers-color-scheme honoured"            "$(grep -q 'prefers-color-scheme' <<<"$flat" && echo 0 || echo 1)"
chk "manual theme toggle present"              "$(grep -qE 'theme|dark-?mode' <<<"$flat" && grep -qE 'addeventlistener|onclick' <<<"$flat" && echo 0 || echo 1)"
chk "email input present"                      "$(grep -qE '<input[^>]+type=["'\'']?email' <<<"$flat" && echo 0 || echo 1)"
chk "form submit intercepted (preventDefault)" "$(grep -q 'preventdefault' <<<"$flat" && echo 0 || echo 1)"
chk "JS email validation (regex or checkValidity)" "$(grep -qE 'checkvalidity|@|\\.test\(|regex' <<<"$flat" && echo 0 || echo 1)"
chk ">=4 FAQ toggles"                          "$([ "$(grep -o '<summary' <<<"$flat" | wc -l | tr -d ' ')" -ge 4 ] || [ "$(grep -o 'aria-expanded' <<<"$flat" | wc -l | tr -d ' ')" -ge 4 ] && echo 0 || echo 1)"
chk "3 pricing tiers mentioned"                "$([ "$(grep -oE '\$[0-9]+' <<<"$flat" | sort -u | wc -l | tr -d ' ')" -ge 3 ] || [ "$(grep -oc 'price\|tier\|plan' <<<"$flat" | head -1)" -ge 3 ] && echo 0 || echo 1)"
chk "focus styles defined"                     "$(grep -qE ':focus' <<<"$flat" && echo 0 || echo 1)"
chk "no lorem ipsum"                           "$(grep -q 'lorem ipsum' <<<"$flat" && echo 1 || echo 0)"
chk "viewport meta present"                    "$(grep -q 'name="viewport"\|name=viewport' <<<"$flat" && echo 0 || echo 1)"
chk "labels for form controls"                 "$(grep -qE '<label|aria-label' <<<"$flat" && echo 0 || echo 1)"
soft "header is sticky"                        "$(grep -qE 'position:\s*sticky' <<<"$flat" && echo 0 || echo 1)"
soft "responsive media query present"          "$(grep -q '@media' <<<"$flat" && echo 0 || echo 1)"

exit $fail
