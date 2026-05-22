import os, sys, openpyxl
sys.stdout.reconfigure(encoding='utf-8')

output_dir = 'outputs'
all_f = os.listdir(output_dir)
new_name = sorted([f for f in all_f if '4166' in f and '20260521' in f and f.endswith('.xlsx') and 'CHECKPOINT' not in f])[-1]
old_name = [f for f in all_f if '4166' in f and '20260507_214702' in f and f.endswith('.xlsx')][0]

for label, fname in [('OLD', old_name), ('NEW', new_name)]:
    path = os.path.join(output_dir, fname)
    wb = openpyxl.load_workbook(path, data_only=True)
    for sn in wb.sheetnames:
        if sn in ('Summary', 'Traceability', 'Combinations'):
            continue
        ws = wb[sn]
        print(f"\n{label} — Sheet: {sn}")
        print(f"  Headers (row 2):")
        for c in range(1, 12):
            v = ws.cell(2, c).value
            if v:
                print(f"    Col {c}: {v}")
        print(f"  Row 3 (first TC):")
        for c in range(1, 12):
            v = str(ws.cell(3, c).value or '')[:100]
            if v:
                print(f"    Col {c}: {v}")
        break
    wb.close()
