package org.example.ticket.performance.dto;
import jakarta.validation.constraints.*;
import org.example.ticket.venue.domain.SeatGrade;
import java.util.Map;
public record CreatePerformanceRequest(@NotBlank @Size(max=160) String title,
                                       @NotEmpty Map<SeatGrade, @Positive Integer> prices) {}
