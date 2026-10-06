create or replace function public.master_tax_on(_person integer, _date date)
returns numeric
language sql
stable
as $$
  -- Налог мастера на дату: история hrm.master_tax_history, иначе текущее поле карточки.
  select coalesce(
    (select h.tax_percent
       from hrm.master_tax_history h
      where h.master = _person and h.start_date <= _date
      order by h.start_date desc
      limit 1),
    (select nullif(c.people__tax::text, '')::numeric from base.contacts c where c.id = _person)
  )
$$;