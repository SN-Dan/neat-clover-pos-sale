# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class CloverTerminalRefundWizard(models.TransientModel):
    _name = 'clover.terminal.refund.wizard'
    _description = 'Clover Terminal Refund Wizard'

    sale_order_id = fields.Many2one('sale.order', readonly=True)
    invoice_id = fields.Many2one('account.move', readonly=True)
    payment_method_id = fields.Many2one(
        'pos.payment.method',
        string='Terminal',
        required=True,
        domain=[('use_payment_terminal', '=', 'neatclover')],
    )
    payment_method_locked = fields.Boolean(default=False, readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    amount_available = fields.Monetary(string='Available to Refund', currency_field='currency_id', readonly=True)
    amount = fields.Monetary(string='Refund Amount', currency_field='currency_id', required=True)
    order_id = fields.Char(readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('done', 'Done'),
    ], default='draft', readonly=True)
    transaction_id = fields.Char(readonly=True)
    payment_page_html = fields.Html(string='Refund', sanitize=False, compute='_compute_payment_page_html')

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        helpers = self.env['clover.document.payment']
        sale_order = self.env['sale.order'].browse(self.env.context.get('default_sale_order_id')).exists()
        invoice = self.env['account.move'].browse(self.env.context.get('default_invoice_id')).exists()

        if sale_order:
            order_id = helpers.order_id_for_sale_order(sale_order)
            currency = sale_order.currency_id
            helpers.assert_document_fully_paid_for_refund(sale_order=sale_order)
        elif invoice:
            if invoice.state != 'posted':
                raise ValidationError(_('Refunds are only available for posted invoices.'))
            order_id = helpers.order_id_for_invoice(invoice)
            currency = invoice.currency_id
            helpers.assert_document_fully_paid_for_refund(invoice=invoice)
        else:
            raise ValidationError(_('Please open this wizard from a sales order or invoice.'))

        payment_method = helpers.get_refund_payment_method_for_order(order_id)
        available = 0.0
        if payment_method:
            available = helpers.get_terminal_refundable_amount(
                order_id, payment_method.neat_clover_terminal_device_code,
            )
        if not payment_method or currency.compare_amounts(available, 0) <= 0:
            raise ValidationError(_('No Clover terminal payment is available to refund on this document.'))
        vals.update({
            'sale_order_id': sale_order.id if sale_order else False,
            'invoice_id': invoice.id if invoice else False,
            'order_id': order_id,
            'currency_id': currency.id,
            'payment_method_id': payment_method.id,
            'payment_method_locked': True,
            'amount_available': available,
            'amount': available,
        })
        return vals

    @api.depends('state', 'transaction_id')
    def _compute_payment_page_html(self):
        for wizard in self:
            wizard.payment_page_html = False
            if wizard.state == 'processing' and wizard.transaction_id:
                page_url = '/pos_clover/terminal_payment_page/refund/%s' % wizard.id
                wizard.payment_page_html = (
                    '<div class="clover-terminal-iframe-wrap">'
                    '<iframe class="clover-terminal-iframe" src="%s" '
                    'allow="payment *"></iframe></div>' % page_url
                )

    def action_start_refund(self):
        self.ensure_one()
        if self.currency_id.compare_amounts(self.amount, 0) <= 0:
            raise ValidationError(_('Refund amount must be greater than zero.'))
        if self.currency_id.compare_amounts(self.amount, self.amount_available) > 0:
            raise ValidationError(_('Refund amount cannot exceed the available amount.'))

        payment_method = self.payment_method_id
        if not payment_method.neat_clover_terminal_device_code:
            raise ValidationError(_('The selected terminal has no device code configured.'))
        if self.payment_method_locked:
            locked_method = self.env['clover.document.payment'].get_refund_payment_method_for_order(self.order_id)
            if locked_method and locked_method != payment_method:
                raise ValidationError(_('Refunds must be processed on the terminal that received the payment.'))

        from odoo.addons.pos_neatclover.controllers.main import PosCloverController
        result = PosCloverController().create_payment_request(
            terminal_id=payment_method.neat_clover_terminal_device_code,
            order_id=self.order_id,
            amount=-self.amount,
            user_id=self.env.user.id,
            is_document_payment=True,
        )
        if not result or result.get('status') not in (200, 201):
            raise ValidationError(_('Could not create the terminal refund request.'))

        self.write({
            'state': 'processing',
            'transaction_id': result['data']['transaction_id'],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Refund by Clover Terminal'),
            'res_model': 'clover.terminal.refund.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
            'context': dict(self.env.context, dialog_size='medium'),
        }

    def action_mark_done(self):
        self.write({'state': 'done'})
        return {'type': 'ir.actions.act_window_close'}
