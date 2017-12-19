from __future__ import unicode_literals

from actstream import action

import datetime
import json
import logging
import xlwt

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import SimpleDocTemplate
from reportlab.rl_config import defaultPageSize

from django.conf import settings
from django.contrib import messages
from django.core.paginator import EmptyPage
from django.core.paginator import PageNotAnInteger
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone

from client.models import Company, Detachment
from helpers import get_object_or_None
from helpers import reverse_redirect
from payroll_core.models import PeriodCode
from payroll_rates.models import PayrollRate
from cost_centers.models import CostCenter

from .forms import DuplicateInvoiceItemFormset
from .forms import InvoiceForm
from .forms import InvoiceItemFormset
from .forms import InvoicePaymentForm
from .models import Invoice
from .models import INVOICE_STATUS
from .models import InvoiceItem
from .models import InvoicePayment

logger = logging.getLogger(__name__)


DEDUCT_INVOICE_ITEM_TYPE = ['adjustments_deduct', 'tax']
ADD_INVOICE_ITEM_TYPE = ['allowances', 'adjustments_add', 'sales', 'vat']

ACTIVE_PERIODS = PeriodCode.objects.filter(
    is_active=True).order_by('-period_code')


def select_client(request):
    args = {}
    args['companies'] = Company.objects.filter(
        is_active=True).order_by('full_name')
    args['periods'] = ACTIVE_PERIODS
    return render(request, 'invoice/select_client.html', args)


def onchange_company(request, company):
    args = {}
    company = get_object_or_None(Company, id=company)
    if not company:
        messages.warning(request, 'Company not found.')
    else:
        detachments = company.get_detachments()
        args['detachments'] = detachments
        args['selected_company_id'] = company.id
        costcenters = company.get_costcenters()
        args['costcenters_regular'] = costcenters.filter(augmentation=False)
        args['costcenters_augmentation'] = costcenters.filter(augmentation=True)
    args['companies'] = Company.objects.filter(
        is_active=True).order_by('full_name')
    return render(request, 'invoice/select_client.html', args)

def onchange_detachment(request, detachment):
    args = {}
    detachment = get_object_or_None(Detachment, id=detachment)
    company = detachment.company
    if not detachment:
        messages.warning(request, 'Detachment not found.')
    else:
        detachments = company.get_detachments()
        args['detachments'] = detachments
        args['selected_company_id'] = company.id
        args['selected_detachment_id'] = detachment.id
        costcenters = CostCenter.objects.filter(
            Q(detachment=detachment) | Q(company=company), is_active=True)
        args['costcenters_regular'] = costcenters.filter(augmentation=False)
        args['costcenters_augmentation'] = costcenters.filter(augmentation=True)
    args['companies'] = Company.objects.filter(
        is_active=True).order_by('full_name')
    return render(request, 'invoice/select_client.html', args)

def list_invoices_by_costcenter(request, costcenter):
    args = {}
    page_num = request.GET.get('page')
    status = request.GET.get('status')
    cost_center = get_object_or_None(CostCenter, id=costcenter)
    query = Invoice.objects.active().filter(cost_center__id=cost_center.id)
    if status == 'paid':
        query = query.paid()
    elif status == 'sent':
        query = query.sent()
    elif status == 'overdue':
        query = query.overdue()
    invoices = query.prefetch_related(
        Prefetch(
            'invoicepayment_set',
            to_attr='amount',
            queryset=InvoicePayment.objects.filter(
                is_active=True)
        )
    )
    for inv in invoices:
        total_paid = sum([pay.amount for pay in inv.amount if inv.amount])
        inv.balance = inv.total_amount - total_paid
    invoice_page = page_objects(page_num, invoices)
    args['page'] = invoice_page
    args['status'] = status
    args['statuses'] = INVOICE_STATUS
    args['costcenter'] = cost_center
    if cost_center.detachment:
        args['detachment'] = cost_center.detachment
    else:
        args['company'] = cost_center.company
    return render(request, 'invoice/list_invoices.html', args)
    

def create_invoice(request, costcenter):
    args = {}
    cost_center = get_object_or_None(CostCenter, id=costcenter)
    values = {
        'cost_center': cost_center,
    }
    if cost_center.detachment:
        values['company'] = cost_center.detachment.company
        args['company'] = cost_center.detachment.company
    else:
        values['company'] = cost_center.company
        args['company'] = cost_center.company
    args['invoiceform'] = InvoiceForm(initial=values)
    InvoiceItemFormset.extra = 2
    #args['company'] = company
    args['formset'] = InvoiceItemFormset(prefix='invoiceitem')
    args['add_types'] = ADD_INVOICE_ITEM_TYPE
    args['deduct_types'] = DEDUCT_INVOICE_ITEM_TYPE
    #args['period'] = period
    args['title'] = 'Create Invoice'
    args['costcenter'] = cost_center
    return render(request, 'invoice/create_invoice.html', args)


def save_invoice(request, costcenter):
    args = {}
    cost_center = get_object_or_None(CostCenter, id=costcenter)
    args['costcenter'] = cost_center
    if cost_center.detachment:
        company = cost_center.detachment.company
    elif cost_center.company:
        company = cost_center.company
    args['company'] = company
    total_amount = request.POST.get('total_amount')
    args['invoiceform'] = InvoiceForm(request.POST)
    args['formset'] = InvoiceItemFormset(
        request.POST, prefix='invoiceitem')
    args['title'] = 'Create Invoice'
    args['add_types'] = ADD_INVOICE_ITEM_TYPE
    args['deduct_types'] = DEDUCT_INVOICE_ITEM_TYPE

    invoiceform = InvoiceForm(request.POST)
    if not invoiceform.is_valid():
        messages.error(request, 'Error saving invoice')
        return render(request, 'invoice/create_invoice.html', args)

    formset = InvoiceItemFormset(request.POST, prefix='invoiceitem')
    for form in formset.forms:
        if not form.is_valid():
            messages.error(request, 'Error saving invoice')
            return render(request, 'invoice/create_invoice.html', args)

    invoice = invoiceform.save(commit=False)
    invoice.total_amount = total_amount
    invoice.created_by = request.user
    invoice.modified_by = request.user
    invoice.save()
    invoiceform.save_m2m()

    formset = InvoiceItemFormset(request.POST, prefix='invoiceitem')
    for form in formset.forms:
        if not form.has_changed():
            continue
        else:
            item = form.save(commit=False)
            item.created_by = request.user
            item.modified_by = request.user
            item.invoice = invoice
            item.save()
    messages.success(request, "Invoice saved.")
    action.send(request.user, verb='saved invoice', action_object=invoice)
    return reverse_redirect('invoice:view_invoice', [invoice.id])


def check_pay_levels(request):
    period_id = request.GET.get('period')
    pay_levels = request.GET.getlist('pay_levels[]')
    invoice = request.GET.get('invoice')
    period = None
    warning_msg = ''
    if not period_id:
        logger.error(
            'period_id query parameter is null, unable to get period. '
            'Received request with data {}'.format(request.GET)
        )
    else:
        period = get_object_or_None(PeriodCode, id=period_id)

    if period and pay_levels:
        invoices = Invoice.objects.filter(
            period=period, pay_levels__in=pay_levels, is_active=True
        ).distinct('id')
        if invoice:
            invoices = invoices.exclude(id=invoice)
        if invoices.exists():
            msg = "Warning! The following pay levels already exist " + \
                "in another invoice for period {}:<br/>{}"
            invoices_list = [
                "- {} {}".format(lev.detachment.name, lev.pay_level)
                for i in invoices for lev in i.pay_levels.all()
                if str(lev.id) in pay_levels
            ]
            invoices_str = '<br/>'.join(invoices_list)
            warning_msg = msg.format(period, invoices_str)
    return HttpResponse(
        json.dumps(warning_msg),
        content_type='application/json; charset=UTF-8'
    )


def list_invoices(request):
    args = {}
    page_num = request.GET.get('page')
    status = request.GET.get('status')
    if status == 'paid':
        query = Invoice.objects.active().paid()
    elif status == 'sent':
        query = Invoice.objects.active().sent()
    elif status == 'overdue':
        query = Invoice.objects.active().overdue()
    else:
        query = Invoice.objects.active()

    invoices = query.prefetch_related(
        Prefetch(
            'invoicepayment_set',
            to_attr='amount',
            queryset=InvoicePayment.objects.filter(
                is_active=True).select_related('pay_levels')
        )
    )
    for inv in invoices:
        total_paid = sum([pay.amount for pay in inv.amount if inv.amount])
        inv.balance = inv.total_amount - total_paid
    invoice_page = page_objects(page_num, invoices)
    args['page'] = invoice_page
    args['status'] = status
    args['statuses'] = INVOICE_STATUS
    return render(request, 'invoice/list_invoices.html', args)


def search_invoice(request):
    args = {}
    search_string = request.GET.get('search_term', '')
    page_num = request.GET.get('page')
    if search_string:
        query = Invoice.objects.active().distinct().filter(
            Q(pay_levels__pay_level__icontains=search_string) |
            Q(pay_levels__detachment__name__icontains=search_string) |
            Q(company__full_name__icontains=search_string) |
            Q(invoice_number__icontains=search_string)
        )
    else:
        query = Invoice.objects.active()

    invoices = query.prefetch_related(
        Prefetch(
            'invoicepayment_set',
            to_attr='amount',
            queryset=InvoicePayment.objects.filter(
                is_active=True).select_related('amount'))
        )
    for inv in invoices:
        total_paid = sum([pay.amount for pay in inv.amount if inv.amount])
        inv.balance = inv.total_amount - total_paid
    args['page'] = page_objects(page_num, invoices)
    args['search_string'] = search_string
    args['statuses'] = INVOICE_STATUS
    return render(request, 'invoice/list_invoices.html', args)


def view_invoice(request, invoice_id):
    args = {}
    invoice = get_object_or_None(Invoice, pk=invoice_id)
    if not invoice:
        messages.error(request, "Invoice record not found.")
        return reverse_redirect('invoice:list_invoices')
    payments, total_paid = invoice.get_total_paid()
    items = invoice.invoiceitem_set.order_by('pk')
    InvoiceItemFormset.extra = 0
    args['formset'] = InvoiceItemFormset(
        instance=invoice,
        prefix='invoiceitem',
        queryset=items
    )
    args['invoice'] = invoice
    args['period'] = invoice.period
    args['invoiceform'] = InvoiceForm(instance=invoice)
    args['periods'] = ACTIVE_PERIODS
    args['payment_form'] = InvoicePaymentForm()
    args['balance'] = invoice.total_amount - total_paid
    args['add_types'] = ADD_INVOICE_ITEM_TYPE
    args['deduct_types'] = DEDUCT_INVOICE_ITEM_TYPE
    args['pay_levels'] = invoice.pay_levels.all()
    if request.method == 'POST':
        return update_invoice(request, invoice)
    return render(request, 'invoice/invoice_detail.html', args)


def add_pay_level(request):
    result = {}
    invoice_id = request.GET.get('invoice_id')
    company_id = request.GET.get('company_id')
    period_id = request.GET.get('period_id')
    pay_level_ids = request.GET.getlist('pay_levels[]')
    if invoice_id:
        invoice = get_object_or_404(Invoice, pk=invoice_id)
        result['invoice'] = invoice
        company = invoice.company
    else:
        if company_id:
            company = get_object_or_404(Company, pk=company_id)
        else:
            company = ''
    if company:
        if period_id:
            period = get_object_or_404(PeriodCode, pk=period_id)
            query = company.get_pay_levels(period)
        else:
            query = company.get_pay_levels()
        result['pay_levels'] = query.exclude(id__in=pay_level_ids)
    else:
        result['pay_levels'] = ''
    html = render_to_string('invoice/add_pay_level.html', result)
    return HttpResponse(html)


def update_invoice(request, invoice):
    args = {}
    company_id = request.POST.get('company')
    company = get_object_or_None(Company, pk=company_id)
    pay_level_ids = request.POST.getlist('pay_levels')
    total_amount = request.POST.get('total_amount')
    args['company'] = company
    pay_levels = PayrollRate.objects.filter(id__in=pay_level_ids)
    args['pay_levels'] = pay_levels
    payments, total_paid = invoice.get_total_paid()
    args['balance'] = invoice.total_amount - total_paid
    args['add_types'] = ADD_INVOICE_ITEM_TYPE
    args['deduct_types'] = DEDUCT_INVOICE_ITEM_TYPE
    args['invoice'] = invoice
    invoiceform = InvoiceForm(request.POST, instance=invoice)
    formset = InvoiceItemFormset(
        request.POST, prefix='invoiceitem', instance=invoice)
    args['formset'] = formset
    args['invoiceform'] = invoiceform

    if not invoiceform.is_valid():
        messages.error(request, "Error updating invoice")
        return render(request, 'invoice/invoice_detail.html', args)

    for form in formset.forms:
        if not form.is_valid():
            messages.error(request, "Error updating invoice")
            return render(request, 'invoice/invoice_detail.html', args)

    invoice = invoiceform.save(commit=False)
    invoice.total_amount = total_amount
    invoice.company = company
    invoice.modified_by = request.user
    invoice.update_fully_paid()
    invoiceform.save_m2m()
    for form in formset.forms:
        if form in formset.deleted_forms:
            form.instance.delete()
        else:
            item = form.save(commit=False)
            item.created_by = request.user
            item.modified_by = request.user
            item.invoice = invoice
            item.save()
    messages.success(request, "Invoice updated.")
    action.send(request.user, verb='updated invoice', action_object=invoice)
    return reverse_redirect('invoice:view_invoice', [invoice.id])


def duplicate_invoice(request, invoice_id):
    args = {}
    invoice = get_object_or_None(Invoice, pk=invoice_id)
    if not invoice:
        messages.error(request, "Invoice record not found.")
        return reverse_redirect('invoice:list_invoices')
    args['invoiceform'] = InvoiceForm(instance=invoice)
    initial = []
    items = InvoiceItem.objects.filter(
        invoice=invoice, is_active=True).order_by('pk')
    for i in items:
        initial.append({
            'type': i.type,
            'description': i.description,
            'equivalent_guard_shift': i.equivalent_guard_shift,
            'hours': i.hours,
            'rate': i.rate,
            'amount': i.amount
        })
    DuplicateInvoiceItemFormset.extra = len(items)
    invoiceitem_formset = DuplicateInvoiceItemFormset(
        queryset=InvoiceItem.objects.none(),
        prefix='invoiceitem',
        initial=initial
    )
    args['formset'] = invoiceitem_formset
    args['add_types'] = ADD_INVOICE_ITEM_TYPE
    args['deduct_types'] = DEDUCT_INVOICE_ITEM_TYPE
    args['invoice'] = invoice
    args['title'] = 'Duplicate Invoice'

    if request.method == 'POST':
        total_amount = request.POST.get('total_amount')
        invoiceform = InvoiceForm(request.POST or None)
        formset = DuplicateInvoiceItemFormset(
            request.POST, prefix='invoiceitem')
        args['invoiceform'] = invoiceform
        args['formset'] = formset
        if not invoiceform.is_valid():
            messages.error(request, "Error duplicating invoice.")
            return render(request, 'invoice/create_invoice.html', args)
        for form in formset.forms:
            if not form.is_valid():
                messages.error(request, "Error duplicating invoice.")
                return render(request, 'invoice/create_invoice.html', args)

        invoice = invoiceform.save(commit=False)
        invoice.total_amount = total_amount
        invoice.created_by = request.user
        invoice.modified_by = request.user
        invoice.pk = None
        invoice.save()
        invoiceform.save_m2m()

        for form in formset.forms:
            if form.is_valid():
                item = form.save(commit=False)
                item.created_by = request.user
                item.modified_by = request.user
                item.invoice = invoice
                item.save()
        messages.success(request, "Invoice duplicated.")
        action.send(request.user, verb='duplicated', action_object=invoice)
        return reverse_redirect('invoice:view_invoice', [invoice.id])
    return render(request, 'invoice/create_invoice.html', args)


def delete_invoice(request, invoice_id):
    invoice = get_object_or_None(Invoice, pk=invoice_id)
    if not invoice:
        messages.error(request, "Invoice record not found.")
        return reverse_redirect('invoice:list_invoices')
    invoice.is_active = False
    invoice.detachment = timezone.now()
    invoice.modified_by = request.user
    invoice.save()
    msg = "Invoice {0} deleted.".format(invoice)
    messages.add_message(request, settings.DELETE_MESSAGE, msg)
    action.send(
        request.user, verb='deleted invoice record', action_object=invoice)
    return reverse_redirect('invoice:list_invoices')


def cancel_invoice(request, invoice_id):
    invoice = get_object_or_None(Invoice, pk=invoice_id)
    if not invoice:
        messages.error(request, "Invoice record not found.")
        return reverse_redirect('invoice:list_invoices')
    invoice.cancelled = True
    invoice.modified_by = request.user
    invoice.save()
    return reverse_redirect('invoice:view_invoice', [invoice.id])


def payment_history(request, invoice_id):
    args = {}
    invoice = get_object_or_None(Invoice, pk=invoice_id)
    payments, total_paid = invoice.get_total_paid()
    args['payments'] = payments
    args['total_paid'] = total_paid
    return render(request, 'invoice/payment_history.html', args)


def create_payment(request, invoice_id):
    args = {}
    args['form'] = InvoicePaymentForm()
    invoice = get_object_or_404(Invoice, pk=invoice_id)
    args['invoice'] = invoice
    if request.method == 'POST':
        form = InvoicePaymentForm(request.POST)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.created_by = request.user
            payment.invoice = invoice
            payment.modified_by = request.user
            payment.save()
            invoice.update_fully_paid()
            if invoice.fully_paid:
                message = 'Invoice is fully paid!'
            else:
                message = 'Payment recorded.'
            messages.success(request, message)
        else:
            if '__all__' in form.errors:
                for error in form.errors['__all__'].as_data():
                    messages.error(request, error[0])
        return reverse_redirect('invoice:view_invoice', [invoice.id])
    return render(request, 'invoice/create_payment.html', args)


def delete_payment(request, payment_id):
    payment = get_object_or_404(
        InvoicePayment.objects.select_related(), pk=payment_id)
    payment.is_active = False
    payment.modified_by = request.user
    payment.save()
    invoice = payment.invoice
    invoice.update_fully_paid()
    msg = "Payment {0} deleted.".format(payment)
    messages.add_message(request, settings.DELETE_MESSAGE, msg)
    action.send(
        request.user, verb='deleted payment record', action_object=payment)
    return reverse_redirect('invoice:view_invoice', [payment.invoice.id])


def footer(canvas, doc):
    PAGE_WIDTH = defaultPageSize[0]
    canvas.saveState()
    canvas.setFont('Times-Bold', 11)
    string = '{:,}'.format(doc.total_amount)
    canvas.drawRightString(PAGE_WIDTH - .5 * cm, 5 * cm, string)
    if doc.firstname and doc.lastname:
        user = u"{0} {1}".format(doc.firstname, doc.lastname)
        name_width = stringWidth(user, 'Times-Bold', 11)
        billing_label = u"Billing Officer"
        billing_label_width = stringWidth(billing_label, 'Times-Roman', 11)
        canvas.drawString((PAGE_WIDTH-name_width) / 2.0, 4.2 * cm, user)
        canvas.drawString(
            (PAGE_WIDTH-billing_label_width) / 2.0, 3.8 * cm, billing_label)
    else:
        user = "BILLING OFFICER: No name found. "
        user += "Please add a first & a last name for the logged in user"
        canvas.setFillColorRGB(255, 0, 0)
        canvas.drawString(PAGE_WIDTH-17.2 * cm, 4.2 * cm, user)
    canvas.restoreState()
    canvas.setTitle('Invoice Print Preview')


def print_invoice(request, invoice_id):
    invoice_objects = Invoice.objects.prefetch_related('invoiceitem_set')
    invoice = get_object_or_404(invoice_objects, pk=invoice_id)
    file = 'invoice_' + str(invoice.company) + invoice.period.display
    filename = 'filename={}.pdf'.format(
        file.replace(".", "").replace(',', '').replace(' ', '_'))
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = filename
    doc = SimpleDocTemplate(
        response, rightMargin=.6 * cm, leftMargin=2 * cm,
        topMargin=2.75 * cm, bottomMargin=3 * cm, pagesize=letter
    )
    elements = invoice.print_invoice_data()
    doc.firstname = request.user.first_name
    doc.lastname = request.user.last_name
    doc.total_amount = invoice.total_amount
    doc.build(elements, onFirstPage=footer)
    return response


def reports(request):
    return render(request, 'invoice/reports.html')


def sales_report(request):
    """
    Generates sales report by period with the ff columns
    Period, Client, Gross Sales, VAT, Withholding Tax, Allowance, Others
    """
    args = {}
    start_period = request.GET.get('start_period')
    end_period = request.GET.get('end_period')
    period_codes = ACTIVE_PERIODS
    if start_period and end_period:
        args['selected_start'] = int(start_period)
        args['selected_end'] = int(end_period)
        args['period_codes'] = period_codes
        result = Invoice.objects.active().filter(
            period__id__gte=start_period,
            period__id__lte=end_period
        ).exclude(
            cancelled=True
        ).prefetch_related('invoiceitem_set')

        wb = xlwt.Workbook()
        sheet1 = wb.add_sheet('Sheet1')
        font = xlwt.Font()
        font.bold = True
        alignment = xlwt.Alignment()
        alignment.horz = xlwt.Alignment.HORZ_CENTER
        style = xlwt.XFStyle()
        style.alignment = alignment
        style.font = font
        detachment_col = sheet1.col(2)
        detachment_col.width = 256 * 40

        header = [
            'Period', 'Client', 'Pay Levels', 'Gross Sales',
            'VAT', 'Witholding Tax', 'Allowances',
            'Adjustments(+)', 'Adjustments(-)'
        ]
        for hcol, hcol_data in enumerate(header):
            sheet1.write(0, hcol, hcol_data, style)
        row = 1

        for inv in result:
            sales, vat, tax, allowances = 0, 0, 0, 0
            adjustments_add, adjustments_deduct = 0, 0
            items = inv.invoiceitem_set.filter(is_active=True)
            period = inv.period.display
            company = inv.company.get_name()
            pay_levels = ', '.join([i.display for i in inv.pay_levels.all()])
            for item in items:
                if item.type == 'sales':
                    sales += item.amount
                elif item.type == 'vat':
                    vat += item.amount
                elif item.type == 'tax':
                    tax += item.amount
                elif item.type == 'allowances':
                    allowances += item.amount
                elif item.type == 'adjustments_add':
                    adjustments_add += item.amount
                elif item.type == 'adjustments_deduct':
                    adjustments_deduct = item.amount
            row_data = (
                period, company, pay_levels, sales, vat, tax,
                allowances, adjustments_add, adjustments_deduct
            )

            col = 0
            style = xlwt.Style.default_style
            style.num_format_str = '#,##0.00'
            for item in row_data:
                sheet1.write(row, col, item, style)
                col += 1
            row += 1

        today = datetime.date.today()
        filename = 'sales-report-{}'.format(today)
        response = HttpResponse(content_type="application/vnd.ms-excel")
        file = 'attachment; filename={}.xls'.format(filename)
        response['Content-Disposition'] = file
        wb.save(response)
        return response

    else:
        args['period_codes'] = period_codes
    return render(request, 'invoice/sales_report.html', args)


def accounts_receivable(request):
    headers = [
        "Company", "Not yet due", "Less than 30 days due", "30 days due",
        "31 to 60 days due", "61 to 90 days due", "Over 90 days due",
        "Total outstanding balance"
    ]
    wb = xlwt.Workbook()
    sheet1 = wb.add_sheet('Sheet1')
    font = xlwt.Font()
    font.bold = True
    alignment = xlwt.Alignment()
    alignment.horz = xlwt.Alignment.HORZ_CENTER
    style = xlwt.XFStyle()
    style.alignment = alignment
    style.font = font

    for hcol, hcol_data in enumerate(headers):
        sheet1.write(0, hcol, hcol_data, style)

    today = datetime.date.today()
    query = Invoice.objects.active()
    companies = Company.objects.filter(is_active=True).prefetch_related(
        Prefetch('invoice_set', queryset=query))
    row = 1
    for co in companies:
        row_data = co.accounts_receivable()
        if row_data:
            col = 0
            for item in row_data:
                sheet1.write(row, col, item, style=xlwt.Style.default_style)
                sheet1.col(col).width = 5500
                col += 1
            row += 1

    today = datetime.date.today()
    filename = 'accounts-receivable-{}'.format(today)
    response = HttpResponse(content_type="application/vnd.ms-excel")
    response['Content-Disposition'] = 'attachment; filename=%s.xls' % filename
    wb.save(response)
    return response


def page_objects(page, objects):
    paginator = Paginator(objects, 10)
    try:
        page = paginator.page(page)
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        page = paginator.page(1)
    return page
