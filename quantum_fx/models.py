from openerp.osv import fields, osv
from openerp.tools.translate import _
import openerp.addons.decimal_precision as dp
from openerp.tools.float_utils import float_round
from datetime import date, datetime
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT, DEFAULT_SERVER_DATE_FORMAT
from openerp import SUPERUSER_ID, api
import openerp
import logging
_logger = logging.getLogger(__name__)


class account_move_line(osv.osv):
    _inherit = "account.move.line"

    def write(self, cr, uid, ids, vals, context=None, check=True, update_check=True):
        #if there exists a debit field in vals and the value of that field is equal to the current debit field value
        #then drop it from the vals dictonary to prevent triggering of "illegal field" error
        for rec in self.pool.get('account.move.line').browse(cr, uid, ids):
            if vals.has_key('debit'):
                if (rec.debit-vals['debit']) == 0:
                    del vals['debit']

            #if there exists a credit field in vals and the value of that field is equal to the current credit field value
            #then drop it from the vals dictonary to prevent triggering of "illegal field" error
            if vals.has_key('credit'):
                if (rec.credit-vals['credit']) == 0:
                    del vals['credit']

        #Call the base write with vals probably modified
        return super(account_move_line, self).write(cr, uid, ids, vals, context, check, update_check)

    # def _cleanup_recon(self,cr,uid,ids,field,arg,context=None):
    #     res ={}
    #     for amove in self.browse(cr,uid,ids,context=context):
    #         if (amove.credit or 0)+(amove.debit or 0)<=10.0 and not(amove.reconcile_ref):
    #             res[amove.id]=True
    #         else:
    #             res[amove.id]=False
    #         return res


class account_voucher(osv.osv):
    _inherit = 'account.voucher'

    _columns = {
        'schedule_validate':fields.boolean(string='Schedule for Validation', default=False,help='Validation will be by a background process'),
        'filter_limit':fields.float(string='Open Balance Limit', default=0,help='Filter opening balance less or equal to specificed value. Zero value implies no filter')
    }

    def process_scheduled_validate(self,cr,uid, ids=None, context=None):
        ids = self.pool.get('account.voucher').search(cr,uid,[('schedule_validate','=',True),('state','=','draft')])
        for av in self.pool.get('account.voucher').browse(cr,uid,ids):
            try:
                _logger.info('******* START VALIDATING :'+str(av.reference)+' **********')
                av.proforma_voucher()
                av.write({'id':av.id,'schedule_validate':False})
                self.pool.get('mail.message').create(cr, uid,{
                    'subject':'Scheduled Validation Successful',
                    'type': 'notification',
                    'model': 'account.voucher',
                    'res_id': av.id})
                _logger.info('******* FINISHED VALIDATING :'+str(av.reference)+' **********')
                #One at a time
                return True
            except Exception, e:
                _logger.info('******* ERROR VALIDATING :'+str(av.reference)+' **********')
                _logger.info('******* ERROM MSG: '+str(e.name)+' **********')
                av.write({'id': av.id,'schedule_validate': False})
                self.pool.get('mail.message').create(cr, uid,{
                    'subject':'Scheduled Validation Failed',
                    'type': 'notification',
                    'model': 'account.voucher',
                    'res_id': av.id,
                    'body': str(e.value)})
                av.cancel_voucher()



    def onchange_partner_id(self, cr, uid, ids, partner_id, journal_id, amount, currency_id, ttype, date,filter_limit, context=None):
        if context is None:
            ctx={}
        else:
            ctx=context.copy()
        ctx['filter_limit']=filter_limit
        return super(account_voucher,self).onchange_partner_id(cr, uid, ids, partner_id, journal_id, amount, currency_id, ttype, date,ctx)

    def onchange_amount(self, cr, uid, ids, amount, rate, partner_id, journal_id, currency_id, ttype, date, payment_rate_currency_id, company_id,filter_limit, context=None):
        if context is None:
            ctx={}
        else:
            ctx=context.copy()
        ctx['filter_limit']=filter_limit
        return super(account_voucher,self).onchange_amount(cr, uid, ids, amount, rate, partner_id, journal_id, currency_id, ttype, date, payment_rate_currency_id, company_id, ctx)


    def recompute_voucher_lines(self, cr, uid, ids, partner_id, journal_id, price, currency_id, ttype, date, context=None):
        """
        Returns a dict that contains new values and context

        @param partner_id: latest value from user input for field partner_id
        @param args: other arguments
        @param context: context arguments, like lang, time zone

        @return: Returns a dict which contains new values, and context
        """
        def _remove_noise_in_o2m():
            """if the line is partially reconciled, then we must pay attention to display it only once and
                in the good o2m.
                This function returns True if the line is considered as noise and should not be displayed
            """
            if line.reconcile_partial_id:
                if currency_id == line.currency_id.id:
                    if line.amount_residual_currency <= 0:
                        return True
                else:
                    if line.amount_residual <= 0:
                        return True
            return False

        if context is None:
            context = {}
        context_multi_currency = context.copy()

        currency_pool = self.pool.get('res.currency')
        move_line_pool = self.pool.get('account.move.line')
        partner_pool = self.pool.get('res.partner')
        journal_pool = self.pool.get('account.journal')
        line_pool = self.pool.get('account.voucher.line')

        #set default values
        default = {
            'value': {'line_dr_ids': [], 'line_cr_ids': [], 'pre_line': False},
        }

        filter_limit = 0
        if context.has_key('filter_limit'):
            filter_limit=context['filter_limit']


        # drop existing lines
        line_ids = ids and line_pool.search(cr, uid, [('voucher_id', '=', ids[0])])
        for line in line_pool.browse(cr, uid, line_ids, context=context):
            if line.type == 'cr':
                default['value']['line_cr_ids'].append((2, line.id))
            else:
                default['value']['line_dr_ids'].append((2, line.id))

        if not partner_id or not journal_id:
            return default

        journal = journal_pool.browse(cr, uid, journal_id, context=context)
        partner = partner_pool.browse(cr, uid, partner_id, context=context)
        currency_id = currency_id or journal.company_id.currency_id.id

        total_credit = 0.0
        total_debit = 0.0
        account_type = None
        if context.get('account_id'):
            account_type = self.pool['account.account'].browse(cr, uid, context['account_id'], context=context).type
        if ttype == 'payment':
            if not account_type:
                account_type = 'payable'
            total_debit = price or 0.0
        else:
            total_credit = price or 0.0
            if not account_type:
                account_type = 'receivable'

        if not context.get('move_line_ids', False):
            ids = move_line_pool.search(cr, uid, [('state','=','valid'), ('account_id.type', '=', account_type), ('reconcile_id', '=', False), ('partner_id', '=', partner_id)], context=context)
        else:
            ids = context['move_line_ids']
        invoice_id = context.get('invoice_id', False)
        company_currency = journal.company_id.currency_id.id
        move_lines_found = []

        #order the lines by most old first
        ids.reverse()
        account_move_lines = move_line_pool.browse(cr, uid, ids, context=context)

        #compute the total debit/credit and look for a matching open amount or invoice
        for line in account_move_lines:
            #filter out line
            if line.amount_residual>=filter_limit and filter_limit!=0:
                continue

            if _remove_noise_in_o2m():
                continue

            if invoice_id:
                if line.invoice.id == invoice_id:
                    #if the invoice linked to the voucher line is equal to the invoice_id in context
                    #then we assign the amount on that line, whatever the other voucher lines
                    move_lines_found.append(line.id)
            elif currency_id == company_currency:
                #otherwise treatments is the same but with other field names
                if line.amount_residual == price:
                    #if the amount residual is equal the amount voucher, we assign it to that voucher
                    #line, whatever the other voucher lines
                    move_lines_found.append(line.id)
                    break
                #otherwise we will split the voucher amount on each line (by most old first)
                total_credit += line.credit or 0.0
                total_debit += line.debit or 0.0
            elif currency_id == line.currency_id.id:
                if line.amount_residual_currency == price:
                    move_lines_found.append(line.id)
                    break
                total_credit += line.credit and line.amount_currency or 0.0
                total_debit += line.debit and line.amount_currency or 0.0

        remaining_amount = price
        #voucher line creation
        for line in account_move_lines:
            #filter out lines
            if line.amount_residual>=filter_limit and filter_limit!=0:
                continue

            if _remove_noise_in_o2m():
                continue

            if line.currency_id and currency_id == line.currency_id.id:
                amount_original = abs(line.amount_currency)
                amount_unreconciled = abs(line.amount_residual_currency)
            else:
                #always use the amount booked in the company currency as the basis of the conversion into the voucher currency
                amount_original = currency_pool.compute(cr, uid, company_currency, currency_id, line.credit or line.debit or 0.0, context=context_multi_currency)
                amount_unreconciled = currency_pool.compute(cr, uid, company_currency, currency_id, abs(line.amount_residual), context=context_multi_currency)
            line_currency_id = line.currency_id and line.currency_id.id or company_currency
            rs = {
                'name':line.move_id.name,
                'type': line.credit and 'dr' or 'cr',
                'move_line_id':line.id,
                'account_id':line.account_id.id,
                'amount_original': amount_original,
                'amount': (line.id in move_lines_found) and min(abs(remaining_amount), amount_unreconciled) or 0.0,
                'date_original':line.date,
                'date_due':line.date_maturity,
                'amount_unreconciled': amount_unreconciled,
                'currency_id': line_currency_id,
            }
            remaining_amount -= rs['amount']
            #in case a corresponding move_line hasn't been found, we now try to assign the voucher amount
            #on existing invoices: we split voucher amount by most old first, but only for lines in the same currency
            if not move_lines_found:
                if currency_id == line_currency_id:
                    if line.credit:
                        amount = min(amount_unreconciled, abs(total_debit))
                        rs['amount'] = amount
                        total_debit -= amount
                    else:
                        amount = min(amount_unreconciled, abs(total_credit))
                        rs['amount'] = amount
                        total_credit -= amount

            if rs['amount_unreconciled'] == rs['amount']:
                rs['reconcile'] = True

            if rs['type'] == 'cr':
                default['value']['line_cr_ids'].append(rs)
            else:
                default['value']['line_dr_ids'].append(rs)

            if len(default['value']['line_cr_ids']) > 0:
                default['value']['pre_line'] = 1
            elif len(default['value']['line_dr_ids']) > 0:
                default['value']['pre_line'] = 1
            default['value']['writeoff_amount'] = self._compute_writeoff_amount(cr, uid, default['value']['line_dr_ids'], default['value']['line_cr_ids'], price, ttype)
        return default




# class stock_quant(osv.osv):
#      _name ="stock.quant"
#      _inherit = "stock.quant"
#
#
#     #THIS METHOD WAS COPIED VERBATIM FROM stock.py AND OVERRIDES THE ONE IN stock_account.py.
#     #BY THIS, THE 'REAL-TIME' VALUATION THAT CAUSE REDUNDANT STOCK JOURNAL ENTRIES IS ELIMINATED FOR PURCHASE TRANSACTIONS.
#     def _quant_create(self, cr, uid, qty, move, lot_id=False, owner_id=False, src_package_id=False, dest_package_id=False, force_location_from=False, force_location_to=False, context=None):
#         '''Create a quant in the destination location and create a negative quant in the source location if it's an internal location.
#         '''
#         if context is None:
#             context = {}
#         price_unit = self.pool.get('stock.move').get_price_unit(cr, uid, move, context=context)
#         location = force_location_to or move.location_dest_id
#         rounding = move.product_id.uom_id.rounding
#         vals = {
#             'product_id': move.product_id.id,
#             'location_id': location.id,
#             'qty': float_round(qty, precision_rounding=rounding),
#             'cost': price_unit,
#             'history_ids': [(4, move.id)],
#             'in_date': datetime.now().strftime(DEFAULT_SERVER_DATETIME_FORMAT),
#             'company_id': move.company_id.id,
#             'lot_id': lot_id,
#             'owner_id': owner_id,
#             'package_id': dest_package_id,
#         }
#
#         if move.location_id.usage == 'internal':
#             #if we were trying to move something from an internal location and reach here (quant creation),
#             #it means that a negative quant has to be created as well.
#             negative_vals = vals.copy()
#             negative_vals['location_id'] = force_location_from and force_location_from.id or move.location_id.id
#             negative_vals['qty'] = float_round(-qty, precision_rounding=rounding)
#             negative_vals['cost'] = price_unit
#             negative_vals['negative_move_id'] = move.id
#             negative_vals['package_id'] = src_package_id
#             negative_quant_id = self.create(cr, SUPERUSER_ID, negative_vals, context=context)
#             vals.update({'propagated_from_id': negative_quant_id})
#
#         #create the quant as superuser, because we want to restrict the creation of quant manually: we should always use this method to create quants
#         quant_id = self.create(cr, SUPERUSER_ID, vals, context=context)
#         return self.browse(cr, uid, quant_id, context=context)
#
#
#     #THIS METHOD WAS COPIED VERBATIM FROM stock.py AND OVERRIDES THE ONE IN stock_account.py.
#     #BY THIS, THE 'REAL-TIME' VALUATION THAT CAUSE REDUNDANT STOCK JOURNAL ENTRIES IS ELIMINATED FOR PURCHASE TRANSACTIONS.
#
#     def move_quants_write(self, cr, uid, quants, move, location_dest_id, dest_package_id, context=None):
#         context=context or {}
#         vals = {'location_id': location_dest_id.id,
#                 'history_ids': [(4, move.id)],
#                 'reservation_id': False}
#         if not context.get('entire_pack'):
#             vals.update({'package_id': dest_package_id})
#         self.write(cr, SUPERUSER_ID, [q.id for q in quants], vals, context=context)
#
#
#     def _quant_create(self, cr, uid, qty, move, lot_id=False, owner_id=False, src_package_id=False, dest_package_id=False, force_location_from=False, force_location_to=False, context=None):
#         quant = super(stock_quant, self)._quant_create(cr, uid, qty, move, lot_id=lot_id, owner_id=owner_id, src_package_id=src_package_id, dest_package_id=dest_package_id, force_location_from=force_location_from, force_location_to=force_location_to, context=context)
#         #if move.product_id.valuation == 'real_time':
#         #    self._account_entry_move(cr, uid, [quant], move, context)
#         return quant
#
#     def move_quants_write(self, cr, uid, quants, move, location_dest_id, dest_package_id, context=None):
#         res = super(stock_quant, self).move_quants_write(cr, uid, quants, move, location_dest_id,  dest_package_id, context=context)
#         #if move.product_id.valuation == 'real_time':
#         #    self._account_entry_move(cr, uid, quants, move, context=context)
#         return res
#
