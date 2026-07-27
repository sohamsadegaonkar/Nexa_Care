import { Paragraph, Select, YStack } from '@my/ui'
import { ChevronDown } from '@tamagui/lucide-icons'
import type { ConsentSelectProps, ConsentSelectValue } from './ConsentSelect.types'

/** Native/Expo implementation. Web resolves ConsentSelect.web.tsx instead. */
export function ConsentSelect<T extends ConsentSelectValue>({
  id,
  label,
  value,
  options,
  onValueChange,
  disabled = false,
  placeholder,
}: ConsentSelectProps<T>) {
  const handleValueChange = (nextValue: string) => {
    const selected = options.find((option) => String(option.value) === nextValue)
    if (selected) onValueChange(selected.value)
  }

  return (
    <YStack gap="$2">
      <Paragraph
        color="$color11"
        fontSize={15}
      >
        {label}
      </Paragraph>
      <Select
        value={String(value)}
        onValueChange={handleValueChange}
        disablePreventBodyScroll
      >
        <Select.Trigger
          id={id}
          size="$4"
          iconAfter={ChevronDown}
          disabled={disabled}
        >
          <Select.Value placeholder={placeholder ?? `Select ${label.toLowerCase()}`} />
        </Select.Trigger>
        <Select.Content>
          <Select.Viewport>
            {options.map((option, index) => (
              <Select.Item
                key={String(option.value)}
                index={index}
                value={String(option.value)}
              >
                <Select.ItemText>{option.label}</Select.ItemText>
              </Select.Item>
            ))}
          </Select.Viewport>
        </Select.Content>
      </Select>
    </YStack>
  )
}
