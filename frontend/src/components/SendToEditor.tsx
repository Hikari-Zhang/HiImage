import { useNavigate } from 'react-router-dom'
import { editorDestinations, transferImageToEditor, type EditorDestination } from '../services/editorTransfer'
import Select from './ui/Select'

export default function SendToEditor({ image, current }: { image: string | null; current: EditorDestination }) {
  const navigate = useNavigate()
  if (!image) return null
  return (
    <Select
      aria-label="将结果发送到其他工具"
      size="sm"
      value=""
      options={[
        { value: '', label: '发送结果到…', disabled: true },
        ...Object.entries(editorDestinations)
          .filter(([id]) => id !== current)
          .map(([value, label]) => ({ value, label })),
      ]}
      onChange={(event) => {
        const destination = event.target.value as EditorDestination
        if (!(destination in editorDestinations)) return
        transferImageToEditor(image, destination)
        navigate(`/${destination}`)
      }}
    />
  )
}
