import { getEntityColumnsByFields } from 'admin/simple_select';

function formatCohort(value) {
    if (value == null) return null;
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) return null;
    const mm = String(dt.getUTCMonth() + 1).padStart(2, '0');
    const yy = String(dt.getUTCFullYear()).slice(-2);
    return mm + yy;
}

// Когорта по дате оплаты
const SUBSCRIPTION_TYPES = ['Абонемент', 'МК по абонементу'];
// Полностью исключаются из когорт
const EXCLUDED_TYPES = ['HEAD', 'ОУ HEAD', 'B2B', 'Сертификат'];

export default async function handleEvent(event, args) {
    let txId = event.source.newId;
    if (event.source.type === 'update') txId = event.source.id;
    if (txId == null) return true;

    const txRows = await getEntityColumnsByFields(
        { schema: 'fin', name: 'transactions' },
        ['customer', 'amount', 'tks_state', 'action', 'request', 'transaction_date', 'real_transaction_datetime'],
        { id: txId }
    );
    if (!txRows.length) return true;

    const tx = txRows[0];
    const state = args.tks_state ?? tx.tks_state;
    if (state !== 'CONFIRMED' && state !== 'AUTHORIZED' && state !== 'SIGNED') return true;

    const amount = args.amount ?? tx.amount;
    if (amount == null || amount < 1e-2) return true;

    const customerId = args.customer ?? tx.customer;
    if (customerId == null) return true;

    const peopleRows = await getEntityColumnsByFields(
        { schema: 'base', name: 'people' },
        ['cohort', 'cohort_b2c', 'cohort_b2c_top'],
        { id: customerId }
    );
    if (!peopleRows.length) return true;

    let actionId = args.action ?? tx.action;
    const requestId = args.request ?? tx.request;
    if (actionId == null && requestId != null) {
        const reqRows = await getEntityColumnsByFields(
            { schema: 'crm', name: 'actions_for_contacts' },
            ['action'],
            { id: requestId }
        );
        if (reqRows.length) actionId = reqRows[0].action;
    }
    if (actionId == null) return true;

    const actionRows = await getEntityColumnsByFields(
        { schema: 'crm', name: 'actions' },
        ['class_type', 'fin_segment', 'pl_start_date'],
        { id: actionId }
    );
    if (!actionRows.length) return true;

    let classTypeName = null;
    let segmentId = actionRows[0].fin_segment;
    const classTypeId = actionRows[0].class_type;
    const plStartDate = actionRows[0].pl_start_date;

    if (classTypeId != null) {
        const classTypeRows = await getEntityColumnsByFields(
            { schema: 'crm', name: 'class_type' },
            ['name', 'segment'],
            { id: classTypeId }
        );
        if (classTypeRows.length) {
            classTypeName = classTypeRows[0].name;
            if (segmentId == null) segmentId = classTypeRows[0].segment;
        }
    }

    // Исключаем HEAD, ОУ HEAD, B2B, Сертификат
    if (segmentId === 2 || EXCLUDED_TYPES.includes(classTypeName)) return true;

    // Определяем дату для когорты:
    // - абонементы → дата оплаты
    // - всё остальное → pl_start_date (если есть), иначе дата оплаты
    const paidAt = args.real_transaction_datetime
        ?? tx.real_transaction_datetime
        ?? args.transaction_date
        ?? tx.transaction_date;

    const isSubscription = SUBSCRIPTION_TYPES.includes(classTypeName);
    const cohortDate = (isSubscription || plStartDate == null) ? paidAt : plStartDate;

    const cohort = formatCohort(cohortDate);
    if (cohort == null) return true;

    const person = peopleRows[0];
    const update = {};
    if (person.cohort == null) update.cohort = cohort;

    if (classTypeName === 'HEAD') {
        if (person.cohort_b2c_top == null) update.cohort_b2c_top = cohort;
    } else {
        if (person.cohort_b2c == null) update.cohort_b2c = cohort;
    }

    if (Object.keys(update).length) {
        await FunDB.updateEntity({ schema: 'base', name: 'people' }, customerId, update);
    }

    return true;
}