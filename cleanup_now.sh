#!/bin/bash
# Quick cleanup - delete old files now (safe)

echo "TASI Quick Cleanup - $(date)"
echo "================================"

# Delete old chart versions
echo "Deleting old chart files..."
rm -f /home/mino/tasi-exec/flowchart.pdf
rm -f /home/mino/tasi-exec/flowchart.png
rm -f /home/mino/tasi-exec/flowchart.tex
rm -f /home/mino/tasi-exec/flowchart.log
rm -f /home/mino/tasi-exec/flowchart_a1.log
rm -f /home/mino/tasi-exec/flowchart_page-*.png
rm -f /home/mino/tasi-exec/flowchart_a1_v2-1.png
rm -f /home/mino/tasi-exec/flowchart_a1_v3-1.png
rm -f /home/mino/tasi-exec/flowchart_a1_v4-1.png
rm -f /home/mino/tasi-exec/flowchart_timeline-1.png
rm -f /home/mino/tasi-exec/classification_mindmap.png
rm -f /home/mino/tasi-exec/daily_process_map.png
rm -f /home/mino/tasi-exec/strategy_decision_tree.png
rm -f /home/mino/tasi-exec/TASI_Ops_Procedure_2026-05-18.pdf
rm -f /home/mino/tasi-exec/TASI_Ops_Procedure_2026-05-18_v1.3.pdf
rm -f /home/mino/tasi-exec/TASI_Ops_Procedure_2026-05-18_v2.pdf

echo "Old files deleted."
echo ""
echo "Current size:"
du -sh /home/mino/tasi-exec/
