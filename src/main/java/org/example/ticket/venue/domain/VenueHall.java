package org.example.ticket.venue.domain;

import jakarta.persistence.*;
import lombok.*;

@Entity
@Table(name = "venue_hall")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor
@Builder
public class VenueHall {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Column(nullable = false, length = 120)
    private String name;
}
