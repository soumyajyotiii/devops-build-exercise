variable "project" {
  type = string
}

variable "environment" {
  type = string
}

variable "domain" {
  type    = string
  default = "saaffinance.com"
}

variable "from_address" {
  type    = string
  default = "loans@saaffinance.com"
}

variable "tags" {
  type    = map(string)
  default = {}
}
