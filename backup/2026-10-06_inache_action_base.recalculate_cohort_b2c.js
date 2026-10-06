import { getAnonymousUserViewColumns } from 'admin/simple_select';

function formatCohort(value) {
    if (!value) return null;

    const s = typeof value === 'string' ? value : String(value);
    const normalized = s.trim().replaceAll('\\/', '/');
    const datePart = normalized.slice(0, 10).replaceAll('/', '-');
    const m = datePart.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (m) {
        return m[2] + m[1].slice(-2);
    }

    const d = new Date(normalized);
    if (!Number.isNaN(d.getTime())) {
        const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
        const yy = String(d.getUTCFullYear()).slice(-2);
        return mm + yy;
    }

    return null;
}

export default async function handleEvent(args) {
    FunDB.writeEvent('start');

    const rows = await getAnonymousUserViewColumns(
        `SELECT
            p.id AS customer_id,
            p.cohort_b2c AS cohort_b2c_current,
            p.cohort AS cohort_current,
            MIN(
                CASE
                    WHEN t.action=>class_type=>name IN ('Абонемент', 'МК по абонементу')
                        THEN COALESCE(t.real_transaction_datetime, t.transaction_date)
                    WHEN t.action=>pl_start_date IS NOT NULL
                        THEN t.action=>pl_start_date
                    ELSE COALESCE(t.real_transaction_datetime, t.transaction_date)
                END
            ) AS cohort_date
        FROM fin.transactions AS t
        JOIN base.people AS p ON p.id = t.customer
        WHERE
            t.is_deleted = FALSE
            AND t.customer IS NOT NULL
            AND t.amount >= 0.01
            AND t.tks_state IN ('CONFIRMED', 'AUTHORIZED', 'SIGNED')
            AND (t.action=>class_type=>segment IS NULL OR t.action=>class_type=>segment <> 2)
            AND COALESCE(t.action=>class_type=>name, '') NOT IN ('HEAD', 'ОУ HEAD')
            AND UPPER(COALESCE(t.action=>class_type=>name, '')) NOT LIKE '%B2B%'
            AND UPPER(COALESCE(t.action=>class_type=>name, '')) NOT LIKE '%СЕРТИФИКАТ%'
        GROUP BY p.id, p.cohort_b2c, p.cohort
        ORDER BY p.id`
    );

    FunDB.writeEvent('rows: ' + rows.length);

    let updated = 0;
    let unchanged = 0;
    let errors = 0;
    let nulls = 0;

    for (const row of rows) {
        const cid = row.customer_id;
        const cohort = formatCohort(row.cohort_date);

        if (!cid || !cohort) {
            nulls++;
            if (nulls <= 2) FunDB.writeEvent('null: cid=' + cid + ' date=' + JSON.stringify(row.cohort_date));
            continue;
        }

        if (row.cohort_b2c_current === cohort && row.cohort_current === cohort) {
            unchanged++;
            continue;
        }

        try {
            await FunDB.updateEntity({ schema: 'base', name: 'people' }, cid, { cohort_b2c: cohort, cohort: cohort });
            updated++;
        } catch (e) {
            errors++;
            if (errors <= 3) FunDB.writeEvent('err cid=' + cid + ' cohort=' + cohort + ': ' + e.message);
        }
    }

    FunDB.writeEvent('done updated=' + updated + ' unchanged=' + unchanged + ' errors=' + errors + ' nulls=' + nulls);
    return { updated, unchanged, errors, nulls, total: rows.length };
}