export type ConsentSelectValue = string | number

export interface ConsentSelectOption<T extends ConsentSelectValue> {
  value: T
  label: string
}

export interface ConsentSelectProps<T extends ConsentSelectValue> {
  id: string
  label: string
  value: T
  options: readonly ConsentSelectOption<T>[]
  onValueChange: (value: T) => void
  disabled?: boolean
  placeholder?: string
}
