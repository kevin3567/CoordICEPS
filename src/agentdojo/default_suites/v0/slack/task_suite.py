from agentdojo.default_suites.v0.tools.slack import (
    Slack,
    add_user_to_channel,
    get_channels,
    get_users_in_channel,
    invite_user_to_slack,
    read_channel_messages,
    read_inbox,
    remove_user_from_slack,
    send_channel_message,
    send_direct_message,
)
from agentdojo.default_suites.v0.tools.web import Web, get_webpage, post_webpage
from agentdojo.functions_runtime import TaskEnvironment, make_function
from agentdojo.task_suite.task_suite import TaskSuite


class SlackEnvironment(TaskEnvironment):
    slack: Slack
    web: Web


TOOLS = [
    # Slack discovery and reads.
    get_channels,
    get_users_in_channel,
    read_channel_messages,
    read_inbox,
    # Slack messaging and workspace mutations.
    send_direct_message,
    send_channel_message,
    invite_user_to_slack,
    add_user_to_channel,
    remove_user_from_slack,
    # Web reads and writes linked from Slack content.
    get_webpage,
    post_webpage,
]

task_suite = TaskSuite[SlackEnvironment]("slack", SlackEnvironment, [make_function(tool) for tool in TOOLS])
