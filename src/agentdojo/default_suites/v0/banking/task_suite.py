from agentdojo.default_suites.v0.tools.banking_client import (
    BankAccount,
    get_balance,
    get_iban,
    get_most_recent_transactions,
    get_scheduled_transactions,
    schedule_transaction,
    send_money,
    update_scheduled_transaction,
)
from agentdojo.default_suites.v0.tools.calendar_client import Calendar, get_current_day
from agentdojo.default_suites.v0.tools.file_reader import Filesystem, read_file
from agentdojo.default_suites.v0.tools.user_account import UserAccount, get_user_info, update_password, update_user_info
from agentdojo.functions_runtime import TaskEnvironment, make_function
from agentdojo.task_suite.task_suite import TaskSuite


class BankingEnvironment(TaskEnvironment):
    bank_account: BankAccount
    filesystem: Filesystem
    user_account: UserAccount
    calendar: Calendar


TOOLS = [
    # Bank account information.
    get_iban,
    get_balance,
    get_most_recent_transactions,
    get_scheduled_transactions,

    # Money movement and scheduled payments.
    send_money,
    schedule_transaction,
    update_scheduled_transaction,

    # Files used as banking documents and notices.
    read_file,

    # User profile and account credentials.
    get_user_info,
    update_password,
    update_user_info,

    # Date lookup for date-sensitive banking actions.
    get_current_day,
]

task_suite = TaskSuite[BankingEnvironment]("banking", BankingEnvironment, [make_function(tool) for tool in TOOLS])
