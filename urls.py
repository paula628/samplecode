from django.conf.urls import url

from . import views

urlpatterns = [
    url(
        regex=r'^overview/$',
        view=views.list_invoices,
        name='list_invoices'
    ),

    url(
        regex=r'^overview/costcenter/(?P<costcenter>\w+)$',
        view=views.list_invoices_by_costcenter,
        name='list_invoices_by_costcenter'
    ),

    url(
        regex=r'^overview/search/$',
        view=views.search_invoice,
        name='search_invoice'
    ),

    url(
        regex=r'^create/(?P<costcenter>\w+)/$',
        view=views.create_invoice,
        name='create_invoice'
    ),

    url(
        regex=r'^select_client/$',
        view=views.select_client,
        name='select_client'
    ),

    url(
        regex=r'^delete/(?P<invoice_id>\w+)/$',
        view=views.delete_invoice,
        name='delete_invoice'
    ),

    url(
        regex=r'^cancel/(?P<invoice_id>\w+)/$',
        view=views.cancel_invoice,
        name='cancel_invoice'
    ),

    url(
        regex=r'^view/(?P<invoice_id>\w+)/duplicate/$',
        view=views.duplicate_invoice,
        name='duplicate_invoice'
    ),

    url(
        regex=r'^select-client/(?P<company>[\w]+)/$',
        view=views.onchange_company,
        name='onchange_company'
    ),

    url(
        regex=r'^select-client-detachment/(?P<detachment>[\w]+)/$',
        view=views.onchange_detachment,
        name='onchange_detachment'
    ),

    url(
        regex=r'^create/(?P<costcenter>\w+)/main-form/save/$',
        view=views.save_invoice,
        name='save_invoice'
    ),

    url(
        regex=r'^view/(?P<invoice_id>\w+)/$',
        view=views.view_invoice,
        name='view_invoice'
    ),

    url(
        regex=r'^view/(?P<invoice_id>\w+)/payment-history/$',
        view=views.payment_history,
        name='payment_history'
    ),

    url(
        regex=r'^view/(?P<invoice_id>\w+)/create-payment/$',
        view=views.create_payment,
        name='create_payment'
    ),

    url(
        regex=r'^delete-payment/(?P<payment_id>\w+)/$',
        view=views.delete_payment,
        name='delete_payment'
    ),

    url(
        regex=r'^view/add-detachment/$',
        view=views.add_pay_level,
        name='add_pay_level'
    ),

    url(
        regex=r'^print/(?P<invoice_id>\w+)/$',
        view=views.print_invoice,
        name='print_invoice'
    ),

    url(
        regex=r'^reports/$',
        view=views.reports,
        name='reports'
    ),

    url(
        regex=r'^reports/sales-report$',
        view=views.sales_report,
        name='sales_report'
    ),

    url(
        regex=r'^reports/accounts-receivable-report$',
        view=views.accounts_receivable,
        name='accounts_receivable'
    ),

    url(
        regex=r'^check-pay-levels$',
        view=views.check_pay_levels,
        name='check_pay_levels'
    ),
]
