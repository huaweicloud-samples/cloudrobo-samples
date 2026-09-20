#include <memory>
#include <vector>
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/float64.hpp>
#include <std_msgs/msg/float64_multi_array.hpp>

using namespace std;

class Ur5eGripperController : public rclcpp::Node
{
public:
  explicit Ur5eGripperController(const rclcpp::NodeOptions &options = rclcpp::NodeOptions())
      : Node("ur5e_gripper_controller", options)
  {
    // 创建发布者和订阅者
    hand_gripper_publisher_ = this->create_publisher<std_msgs::msg::Float64MultiArray>(
        "/hand_position_controller/commands", 10);
    

    hand_gripper_subscriber_ = this->create_subscription<std_msgs::msg::Float64>(
        "hand_gripper_controller", 10, std::bind(&Ur5eGripperController::HandGripperCallback, this, std::placeholders::_1));

    RCLCPP_INFO(this->get_logger(), "ur5e Gripper Controller is ready to receive commands.");
  }

private:
  rclcpp::Publisher<std_msgs::msg::Float64MultiArray>::SharedPtr hand_gripper_publisher_;

  rclcpp::Subscription<std_msgs::msg::Float64>::SharedPtr hand_gripper_subscriber_;

  void base_control_gripper(const rclcpp::Publisher<std_msgs::msg::Float64MultiArray>::SharedPtr &publisher, float joint_value)
  {
    std::vector<double> state = {
        0.4 * joint_value,   // left_finger
        0.4 * joint_value    // right_finger
    };

    auto message = std_msgs::msg::Float64MultiArray();
    message.data = state;
    publisher->publish(message);
  }


  void HandGripperCallback(const std_msgs::msg::Float64::SharedPtr msg)
  {
    double width = msg->data;

    if (width > 0.1) {  // 假设最大开合是0.1
      width = 0.1;
      RCLCPP_INFO(this->get_logger(), "Opening right gripper");
    } else {
      RCLCPP_INFO(this->get_logger(), "Closing right gripper");
    }

    base_control_gripper(hand_gripper_publisher_, width);
  }
};

int main(int argc, char *argv[])
{
  rclcpp::init(argc, argv);
  rclcpp::executors::MultiThreadedExecutor executor;
  auto node = std::make_shared<Ur5eGripperController>();
  executor.add_node(node);
  executor.spin();
  rclcpp::shutdown();
  return 0;
}