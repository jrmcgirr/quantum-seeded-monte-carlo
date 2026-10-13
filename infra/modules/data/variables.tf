variable "name" {
  description = "Resource name prefix (qmc-<env>)."
  type        = string
}

variable "table_rcu" {
  description = "Provisioned read capacity."
  type        = number
}

variable "table_wcu" {
  description = "Provisioned write capacity."
  type        = number
}

variable "visibility_timeout_s" {
  description = "Main queue visibility timeout in seconds."
  type        = number
}

variable "max_receive_count" {
  description = "Receives before a message moves to the DLQ."
  type        = number
}
