import aws_cdk as core
import aws_cdk.assertions as assertions

from auto_adjudication.auto_adjudication_stack import AutoAdjudicationStack

# example tests. To run these tests, uncomment this file along with the example
# resource in auto_adjudication/auto_adjudication_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = AutoAdjudicationStack(app, "auto-adjudication")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
