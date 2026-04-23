for _, row in df.iterrows():
    try:
        # normalize keys
        row_dict = {str(k).strip(): row[k] for k in df.columns}

        submission = str(row_dict.get("Submission #", "")).strip()

        if not submission:
            continue

        c.execute("""
        INSERT INTO submissions (
            submission_number,
            customer_name,
            contact_info,
            service_type,
            card_count,
            status,
            est_cost,
            prep_needed,
            customer_paid,
            declared_value,
            submission_date
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (submission_number)
        DO UPDATE SET
            status = EXCLUDED.status,
            card_count = EXCLUDED.card_count,
            last_updated = CURRENT_TIMESTAMP
        """, (
            submission,
            str(row_dict.get("Customer Name", "")),
            str(row_dict.get("Contact Info", "")),
            str(row_dict.get("Service Type", "")),
            int(row_dict.get("# Of Cards", 0)) if str(row_dict.get("# Of Cards", "")).isdigit() else 0,
            str(row_dict.get("Current Status", "")),
            str(row_dict.get("Est Cost", "")),
            str(row_dict.get("Prep Needed", "")),
            str(row_dict.get("Customer Paid", "")),
            str(row_dict.get("Declared Value", "")),
            str(row_dict.get("s", "")),
        ))

        count += 1

    except:
        continue
