#!/bin/bash
# Double-click to price a basket of cards (up to 500) from the local
# price-guide CSVs. Put your spreadsheet in the "baskets" folder first.
#
# The spreadsheet needs a column of card names and, ideally, columns for
# Grade and Set:
#
#     Card                          Set                Grade
#     Charizard [1st Edition] #4    Pokemon Base Set   PSA 10
#     Blastoise [1st Edition] #2    Pokemon Base Set   PSA 9
#
# Blank grade means ungraded. No Set column is fine, but a card whose name
# exists in several sets will be reported as ambiguous rather than guessed.
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Run 'Run Scan.command' once first to set up dependencies."
    read -r -p "Press Enter to close..."
    exit 1
fi

mkdir -p baskets
PY=".venv/bin/python"

shopt -s nullglob
FILES=(baskets/*.xlsx baskets/*.xlsm baskets/*.csv)
shopt -u nullglob

echo "=========================================================================="
echo "CUSTOM BASKET PRICER"
echo "=========================================================================="

if [ ${#FILES[@]} -eq 0 ]; then
    echo "The 'baskets' folder is empty."
    echo
    echo "Two ways to start:"
    echo
    echo "  1. Build a whole set automatically, then fill in the grades:"
    echo "       $PY code/basket_pricer.py --seed-set \"Pokemon Base Set\" \\"
    echo "            --variant \"1st Edition\""
    echo
    echo "  2. Make your own spreadsheet with Card / Set / Grade columns"
    echo "     and save it into the 'baskets' folder."
    echo
    read -r -p "Seed the 1st Edition Base Set now? [y/N] " ans
    if [[ "$ans" =~ ^[Yy] ]]; then
        "$PY" code/basket_pricer.py --seed-set "Pokemon Base Set" \
              --variant "1st Edition"
        echo
        echo "Open it, fill in the Grade column, then run this again."
        open baskets 2>/dev/null
    fi
    read -r -p "Press Enter to close..."
    exit 0
fi

if [ ${#FILES[@]} -eq 1 ]; then
    CHOICE="${FILES[0]}"
else
    echo "Which basket?"
    for i in "${!FILES[@]}"; do
        echo "   $((i+1)). $(basename "${FILES[$i]}")"
    done
    echo
    read -r -p "Number [1]: " n
    n="${n:-1}"
    CHOICE="${FILES[$((n-1))]}"
    if [ -z "$CHOICE" ]; then
        echo "Not a valid choice."
        read -r -p "Press Enter to close..."
        exit 1
    fi
fi

echo "Pricing $(basename "$CHOICE")..."
echo
"$PY" code/basket_pricer.py --in "$CHOICE"
RC=$?

if [ $RC -eq 0 ]; then
    OUT=$(ls -t reports/basket_*.xlsx 2>/dev/null | head -1)
    [ -n "$OUT" ] && open "$OUT"
fi

echo
read -r -p "Press Enter to close..."
