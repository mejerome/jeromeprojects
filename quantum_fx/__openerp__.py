# -*- coding: utf-8 -*-
{
    'name': "Quantum FX V8",

    'summary': """
	This module fixes little quirks in Odoo V8 so it works as expected for all Quantum Group Companies.
    """,
    'description': """

Fixes little quirks in Odoo V8 so it works as expected.
=======================================================

This module contains all fixes that applies to all Quantum Group companies.
Company specific fixes should be done in company_fx module.

Fix 1: Journal Entries - Updating Analyic Account Field.
--------------------------------------------------------

This problem arises on update of the analytic field of a Journal Entry (move line).
The system complains of an attempt to modify a legal field after its been reconciled.
The reason is that although the neither the dr or cr fields are modified, the system (erratically) passes it as modified.
The solution is to find the difference between the proposed dr or cr field and the current one.
If there is no difference, drop it from the vals parameter of the update/write function.

 """,

    'author': "Kwesi Smith",
    'website': "http://www.quantumgroupgh.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/master/openerp/addons/base/module/module_data.xml
    # for the full list
    'category': 'Uncategorized',
    'version': '0.5',

    # any module necessary for this one to work correctly
    'depends': ['base','account','account_voucher'],

    # always loaded
    'data': [
        #'security/ir.model.access.csv',
        'templates.xml',
    ],
    # only loaded in demonstration mode
    'demo': [
        #'demo.xml',
    ],
}
